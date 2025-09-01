from flask import Flask, jsonify, request, send_file, after_this_request
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from urllib import parse
import urllib.request
import yt_dlp
import eyed3
from eyed3.id3.frames import ImageFrame
import time
import subprocess
import os
import zipfile
import random
import re
import traceback
from urllib.parse import parse_qs, urlparse
import json

# Crear directorio static si no existe
if not os.path.exists('static'):
    os.makedirs('static')

os.chdir('static')
app = Flask(__name__)

# Configuración mejorada de CORS
CORS(app, resources={
    r"/v1/*": {
        "origins": ["*"],  # En producción, especifica tu dominio
        "methods": ["GET", "POST"],
        "allow_headers": ["Content-Type"]
    }
})

app.config['JSON_AS_ASCII'] = False
socketio = SocketIO(app, cors_allowed_origins='*', ping_interval=100, ping_timeout=5000)

# Variables de entorno para configuración
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5000')

def search_youtube_fallback(query, max_results=10):
    """
    Función de fallback usando requests para buscar en YouTube
    """
    import requests
    
    try:
        # URL de búsqueda de YouTube sin API key
        search_url = "https://www.youtube.com/results"
        params = {
            'search_query': query
        }
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(search_url, params=params, headers=headers)
        
        if response.status_code != 200:
            return []
        
        # Buscar datos JSON en el HTML
        content = response.text
        
        # Extraer el script que contiene los datos de búsqueda
        start_marker = 'var ytInitialData = '
        end_marker = ';</script>'
        
        start_index = content.find(start_marker)
        if start_index == -1:
            return []
        
        start_index += len(start_marker)
        end_index = content.find(end_marker, start_index)
        
        if end_index == -1:
            return []
        
        json_str = content[start_index:end_index]
        data = json.loads(json_str)
        
        results = []
        
        # Navegar por la estructura JSON compleja de YouTube
        try:
            contents = data['contents']['twoColumnSearchResultsRenderer']['primaryContents']['sectionListRenderer']['contents']
            
            for section in contents:
                if 'itemSectionRenderer' in section:
                    items = section['itemSectionRenderer']['contents']
                    
                    for item in items:
                        if 'videoRenderer' in item:
                            video = item['videoRenderer']
                            
                            video_id = video.get('videoId', '')
                            title = video.get('title', {}).get('runs', [{}])[0].get('text', 'Unknown Title')
                            
                            # Channel info
                            channel_name = 'Unknown Channel'
                            if 'ownerText' in video and 'runs' in video['ownerText']:
                                channel_name = video['ownerText']['runs'][0].get('text', 'Unknown Channel')
                            
                            # Duration
                            duration = 'N/A'
                            if 'lengthText' in video:
                                duration = video['lengthText'].get('simpleText', 'N/A')
                            
                            # View count
                            view_count = 0
                            if 'viewCountText' in video:
                                view_text = video['viewCountText'].get('simpleText', '0 views')
                                # Extraer número de vistas
                                import re
                                view_match = re.search(r'([\d,]+)', view_text.replace(',', ''))
                                if view_match:
                                    view_count = int(view_match.group(1))
                            
                            result = {
                                'id': video_id,
                                'title': title,
                                'channel': channel_name,
                                'duration': duration,
                                'view_count': view_count,
                                'thumbnail': f"https://i.ytimg.com/vi/{video_id}/hq720.jpg",
                                'url': f"https://www.youtube.com/watch?v={video_id}"
                            }
                            
                            results.append(result)
                            
                            if len(results) >= max_results:
                                break
                    
                    if len(results) >= max_results:
                        break
        except Exception as parse_error:
            print(f"Error parseando resultados de fallback: {parse_error}")
            return []
        
        return results[:max_results]
        
    except Exception as e:
        print(f"Error en búsqueda fallback: {e}")
        return []

def search_youtube_videos(query, max_results=10):
    """
    Buscar videos de YouTube usando yt-dlp con fallback
    """
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
    }
    
    try:
        # Usar el formato correcto de ytsearch
        search_query = f"ytsearch{max_results}:{query}"
        print(f"Query de búsqueda: {search_query}")
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Buscar videos
            search_results = ydl.extract_info(search_query, download=False)
            
            if not search_results or 'entries' not in search_results:
                print("No se encontraron resultados o no hay entries")
                print("Intentando con método fallback...")
                return search_youtube_fallback(query, max_results)
            
            print(f"Entries encontrados: {len(search_results['entries'])}")
            
            results = []
            for entry in search_results['entries']:
                if entry:
                    print(f"Procesando video: {entry.get('title', 'Sin título')}")
                    result = {
                        'id': entry.get('id', ''),
                        'title': entry.get('title', 'Unknown Title'),
                        'channel': entry.get('uploader', entry.get('channel', 'Unknown Channel')),
                        'duration': entry.get('duration_string', entry.get('duration', 'N/A')),
                        'view_count': entry.get('view_count', 0),
                        'thumbnail': f"https://i.ytimg.com/vi/{entry.get('id', '')}/hq720.jpg",
                        'url': entry.get('url', f"https://www.youtube.com/watch?v={entry.get('id', '')}")
                    }
                    results.append(result)
            
            print(f"Resultados procesados: {len(results)}")
            
            # Si no hay resultados, intentar fallback
            if len(results) == 0:
                print("No hay resultados, intentando fallback...")
                return search_youtube_fallback(query, max_results)
                
            return results
    except Exception as e:
        print(f"Error en búsqueda: {e}")
        traceback.print_exc()
        print("Intentando con método fallback...")
        return search_youtube_fallback(query, max_results)

def get_video_info(video_id):
    """
    Obtener información detallada de un video específico
    """
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            url = f'https://www.youtube.com/watch?v={video_id}'
            info = ydl.extract_info(url, download=False)
            
            return {
                'id': info.get('id', video_id),
                'title': info.get('title', 'Unknown Title'),
                'uploader': info.get('uploader', 'Unknown Channel'),
                'duration': info.get('duration', 0),
                'duration_string': info.get('duration_string', 'N/A'),
                'view_count': info.get('view_count', 0),
                'description': info.get('description', '')[:200] + '...' if info.get('description') else '',
                'thumbnail': info.get('thumbnail', f"https://i.ytimg.com/vi/{video_id}/hq720.jpg"),
                'upload_date': info.get('upload_date', 'N/A')
            }
    except Exception as e:
        print(f"Error obteniendo info del video: {e}")
        return None

def create_link_download_song(data):
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': '%(id)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f'https://www.youtube.com/watch?v={data["video_id"]}', download=True)
            filename = ydl.prepare_filename(info).replace('m4a', 'mp3').replace('webm', 'mp3')

        audiofile = eyed3.load(filename)
        if audiofile.tag is None:
            audiofile.initTag()

        # Descargar imagen de portada
        try:
            urllib.request.urlretrieve(data["metadata"]["cover"], data["video_id"])
            with open(data["video_id"], 'rb') as img_file:
                img_data = img_file.read()
            
            audiofile.tag.images.set(ImageFrame.FRONT_COVER, img_data, 'image/jpeg')
            audiofile.tag.images.set(3, img_data, 'image/jpeg')
            os.remove(data["video_id"])  # Limpiar archivo de imagen
        except Exception as e:
            print(f"Error al descargar portada: {e}")

        # Configurar metadatos
        audiofile.tag.title = data['metadata']['name']
        audiofile.tag.artist = data['metadata']['artist']
        audiofile.tag.album = data['metadata']['album']
        audiofile.tag.save(version=eyed3.id3.ID3_V2_3)

        # URL dinámica
        data["link"] = f"{BASE_URL}/v1/file/{filename}"
        data["tamaño"] = "{} mb".format(round(os.path.getsize(filename) / (1024 * 1024), 2))

        return data
    except Exception as e:
        print(f"Error en create_link_download_song: {e}")
        traceback.print_exc()
        return None

@app.route('/v1/file/<string:audio_file_name>')
def return_audio_file(audio_file_name):
    # Sanitizar nombre de archivo
    audio_file_name = "".join(x for x in audio_file_name if (x.isalnum() or x in "._- ()"))
    
    if audio_file_name.endswith(".mp3") and os.path.isfile(f"{audio_file_name}"):
        try:
            audio = eyed3.load(f"{audio_file_name}")
            download_name = f"{audio.tag.title}.mp3" if audio.tag and audio.tag.title else audio_file_name
            return send_file(f"{audio_file_name}", mimetype="audio/mp3", as_attachment=True,
                           download_name=download_name)
        except Exception:
            return send_file(f"{audio_file_name}", mimetype="audio/mp3", as_attachment=True,
                           download_name=audio_file_name)
    else:
        return jsonify({"error": "Archivo no encontrado"}), 404

@app.route('/v1/song')
def song():
    nombreCancion = request.args.get('name')
    if nombreCancion is None:
        return jsonify({"detail": "Error: Se requiere el parámetro 'name'"}), 400
    
    print(f"nombreCancion: {nombreCancion}")

    try:
        # Buscar usando yt-dlp
        results = search_youtube_videos(nombreCancion, 1)
        
        if not results:
            return jsonify({"detail": "No se encontró la canción"}), 404
            
        video = results[0]
        
        data = {
            "video_id": video['id'], 
            "format": "mp3",
            "metadata": {
                "name": video['title'], 
                "release_date": "YouTube",
                "artist": video['channel'], 
                "album": "YouTube",
                "genre": "N/A", 
                "number": 0,
                "cover": video['thumbnail'], 
                "time": video['duration'],
                "external_link": video['url']
            },
        }

        finaldata = create_link_download_song(data)
        if finaldata is None:
            return jsonify({"detail": "Error al procesar la canción"}), 500
            
        return jsonify(finaldata)
    except Exception as e:
        print(f"Error en /v1/song: {e}")
        traceback.print_exc()
        return jsonify({"detail": f"Error interno del servidor: {str(e)}"}), 500

@app.route('/v1/search/song')
def search_song():
    nombreCancion = request.args.get('name')
    limit = request.args.get('limit', 10)
    
    if nombreCancion is None:
        return jsonify({"detail": "Error: Se requiere el parámetro 'name'"}), 400

    try:
        limit = int(limit)
        if limit > 50:
            limit = 50
    except ValueError:
        limit = 10

    try:
        # Debug: Mostrar la consulta
        print(f"Buscando: {nombreCancion}")
        
        # Usar yt-dlp para buscar
        results = search_youtube_videos(nombreCancion, limit)
        print(f"Resultados encontrados: {len(results)}")
        
        # Formato compatible con el frontend
        songs = []
        for i, video in enumerate(results):
            try:
                # Crear estructura compatible con el frontend
                song_data = {
                    "nombre": video['title'],
                    "artista": video['channel'],
                    "uri": video['title'],  # Usar el título como URI para búsqueda
                    "cover": video['thumbnail'],
                    "external_link": video['url'],
                    "video_id": video['id'],
                    "metadata": {
                        "name": video['title'],
                        "artist": video['channel'],
                        "album": "YouTube",
                        "cover": video['thumbnail'],
                        "duration": video['duration'],
                        "views": f"{video['view_count']:,}" if video['view_count'] else 'N/A',
                        "external_link": video['url']
                    }
                }
                songs.append(song_data)
            except Exception as song_error:
                print(f"Error procesando video {i}: {song_error}")
                continue

        print(f"Canciones procesadas: {len(songs)}")
        return jsonify(songs)
        
    except Exception as e:
        print(f"Error en /v1/search/song: {e}")
        traceback.print_exc()
        return jsonify({"detail": f"Error interno del servidor: {str(e)}"}), 500

@app.route('/v1/playlist')
def playlist():
    playlist_url = request.args.get('url')
    if playlist_url is None:
        return jsonify({"detail": "Error: Se requiere el parámetro 'url'"}), 400
    
    try:
        # Usar yt-dlp para obtener información de playlist
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            playlist_info = ydl.extract_info(playlist_url, download=False)
            
            if not playlist_info or 'entries' not in playlist_info:
                return jsonify({"detail": "No se pudo obtener información de la playlist"}), 400
        
            playlist_data = {
                "name": playlist_info.get('title', 'Playlist sin nombre'),
                "description": f"Playlist con {len(playlist_info['entries'])} canciones",
                "total_songs": len(playlist_info['entries']),
                "cover": f"https://i.ytimg.com/vi/{playlist_info['entries'][0].get('id', '')}/hq720.jpg" if playlist_info['entries'] else None,
                "songs": []
            }

            for i, video in enumerate(playlist_info['entries']):
                if video:  # Algunos entries pueden ser None
                    song_data = {
                        "position": i + 1,
                        "metadata": {
                            "name": video.get('title', 'Título desconocido'),
                            "artist": video.get('uploader', 'Artista desconocido'),
                            "album": playlist_info.get('title', 'Playlist'),
                            "release": "YouTube",
                            "cover": f"https://i.ytimg.com/vi/{video.get('id', '')}/hq720.jpg",
                            "external_link": video.get('url', f"https://www.youtube.com/watch?v={video.get('id', '')}")
                        },
                        "video_id": video.get('id', '')
                    }
                    playlist_data["songs"].append(song_data)

        return jsonify(playlist_data)
    except Exception as e:
        print(f"Error en /v1/playlist: {e}")
        traceback.print_exc()
        return jsonify({"detail": f"Error al procesar la playlist: {str(e)}"}), 500

@app.route('/v1/checkfiles')
def check_files():
    files = [file for file in os.listdir(".") if file.endswith(".mp3")]
    return jsonify({
        "total_files": len(files),
        "files": files
    })

@app.route('/v1/zip')
def create_zip():
    try:
        files = [file for file in os.listdir(".") if file.endswith(".mp3")]
        if not files:
            return jsonify({"error": "No hay archivos MP3 para comprimir"}), 404

        zip_name = f"songs_{random.randint(1000, 9999)}.zip"
        
        with zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file in files:
                zipf.write(file)

        @after_this_request
        def remove_file(response):
            try:
                os.remove(zip_name)
                for file in files:
                    try:
                        os.remove(file)
                    except:
                        pass
            except Exception as error:
                print(f"Error eliminando archivos: {error}")
            return response

        return send_file(zip_name, mimetype='application/zip', as_attachment=True)
    except Exception as e:
        print(f"Error creando ZIP: {e}")
        traceback.print_exc()
        return jsonify({"error": f"Error interno del servidor: {str(e)}"}), 500

# Endpoint para health check
@app.route('/v1/health')
def health_check():
    return jsonify({"status": "ok", "message": "API funcionando correctamente"})

# Endpoint de debug para probar búsquedas
@app.route('/v1/debug/search')
def debug_search():
    query = request.args.get('q', 'test')
    try:
        print(f"DEBUG: Probando búsqueda con '{query}'")
        results = search_youtube_videos(query, 3)
        return jsonify({
            "query": query,
            "results_count": len(results),
            "results": results
        })
    except Exception as e:
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        })

@socketio.on('message')
def handle_message(message):
    songs_send = []
    print(f"Procesando playlist: {message.get('playlist_url')}")
    
    # Usar yt-dlp para descargar playlist completa
    playlist_url = message.get('playlist_url')
    if playlist_url:
        subprocess.Popen(['yt-dlp', '-x', '--audio-format', 'mp3', '--audio-quality', '0', 
                         '--output', '%(title)s.%(ext)s', playlist_url], 
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    songs = message.get('songs', [])
    while len(songs) != len(songs_send):
        for song in songs:
            song_name = song["metadata"]["name"]
            # Limpiar nombre de archivo
            clean_name = re.sub(r'[<>:"/\\|?*]', '', song_name)
            possible_files = [f for f in os.listdir('.') if f.endswith('.mp3') and clean_name.lower() in f.lower()]
            
            if song_name not in songs_send and possible_files:
                try:
                    file_path = possible_files[0]
                    audiofile = eyed3.load(file_path)
                    
                    data = {
                        "song": {"video_id": song.get("video_id", 0), "format": "mp3"},
                        "metadata": {
                            "name": song['metadata']['name'], 
                            "release_date": song['metadata'].get('release', 'N/A'),
                            "artist": song['metadata']['artist'], 
                            "album": song['metadata']['album'],
                            "genre": "N/A", 
                            "number": 0,
                            "cover": song['metadata']['cover'], 
                            "time": 0
                        },
                        'position': song.get('position', 0),
                        'link': f'{BASE_URL}/v1/file/{file_path}',
                        'external_link': song['metadata']['external_link'],
                        'tamaño': "{} mb".format(
                            round(os.path.getsize(file_path) / (1024 * 1024), 2))
                    }

                    emit('message_reply', data)
                    songs_send.append(song_name)
                    socketio.sleep(0.5)
                except Exception as e:
                    print(f"Error procesando canción: {e}")
                    traceback.print_exc()
                    pass
    
    time.sleep(1)
    emit('disconnect')

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)