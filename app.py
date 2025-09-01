from flask import Flask, jsonify, request, send_file, after_this_request
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from urllib import parse
import urllib.request
from youtubesearchpython import VideosSearch, PlaylistsSearch, Playlist
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

def create_link_download_song(data):
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': '%(id)s.%(ext)s'
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
        youtube_song = VideosSearch(nombreCancion, limit=1).result()
        if not youtube_song['result']:
            return jsonify({"detail": "No se encontró la canción"}), 404
            
        data = {
            "video_id": youtube_song['result'][0]['id'], 
            "format": "mp3",
            "metadata": {
                "name": youtube_song['result'][0]['title'], 
                "release_date": youtube_song['result'][0]['publishedTime'],
                "artist": youtube_song['result'][0]['channel']['name'], 
                "album": "YouTube",
                "genre": "N/A", 
                "number": 0,
                "cover": f'https://i.ytimg.com/vi/{youtube_song["result"][0]["id"]}/hq720.jpg', 
                "time": youtube_song['result'][0]['duration'],
                "external_link": f"https://www.youtube.com/watch?v={youtube_song['result'][0]['id']}"
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
        
        youtube_songs = VideosSearch(nombreCancion, limit=limit).result()
        print(f"Resultados encontrados: {len(youtube_songs.get('result', []))}")
        
        # Formato compatible con el frontend
        songs = []
        for i, song in enumerate(youtube_songs.get('result', [])):
            try:
                # Crear estructura compatible con el frontend
                song_data = {
                    "nombre": song['title'],
                    "artista": song['channel']['name'],
                    "uri": song['title'],  # Usar el título como URI para búsqueda
                    "cover": f'https://i.ytimg.com/vi/{song["id"]}/hq720.jpg',
                    "external_link": f"https://www.youtube.com/watch?v={song['id']}",
                    "video_id": song['id'],
                    "metadata": {
                        "name": song['title'],
                        "artist": song['channel']['name'],
                        "album": "YouTube",
                        "cover": f'https://i.ytimg.com/vi/{song["id"]}/hq720.jpg',
                        "duration": song.get('duration', 'N/A'),
                        "views": song.get('viewCount', {}).get('text', 'N/A') if song.get('viewCount') else 'N/A',
                        "external_link": f"https://www.youtube.com/watch?v={song['id']}"
                    }
                }
                songs.append(song_data)
            except Exception as song_error:
                print(f"Error procesando canción {i}: {song_error}")
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
    
    # Extraer ID de playlist de YouTube
    playlist_id = None
    if 'playlist?list=' in playlist_url:
        playlist_id = playlist_url.split('list=')[1].split('&')[0]
    elif 'youtube.com' in playlist_url and 'list=' in playlist_url:
        playlist_id = playlist_url.split('list=')[1].split('&')[0]
    else:
        return jsonify({"detail": "URL de playlist de YouTube inválida"}), 400

    try:
        # Obtener información de la playlist
        playlist = Playlist(f'https://www.youtube.com/playlist?list={playlist_id}')
        
        playlist_data = {
            "name": playlist.title,
            "description": f"Playlist de YouTube con {len(playlist.videos)} canciones",
            "total_songs": len(playlist.videos),
            "cover": playlist.videos[0]['thumbnails'][0]['url'] if playlist.videos else None,
            "songs": []
        }

        for i, video in enumerate(playlist.videos):
            song_data = {
                "position": i + 1,
                "metadata": {
                    "name": video['title'],
                    "artist": video['channel']['name'],
                    "album": playlist.title,
                    "release": video.get('publishedTime', 'N/A'),
                    "cover": video['thumbnails'][0]['url'] if video['thumbnails'] else f'https://i.ytimg.com/vi/{video["id"]}/hq720.jpg',
                    "external_link": f"https://www.youtube.com/watch?v={video['id']}"
                },
                "video_id": video['id']
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