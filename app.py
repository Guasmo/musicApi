from flask import Flask, jsonify, request, send_file, after_this_request
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from urllib import parse
import urllib.request
from youtubesearchpython import VideosSearch
import yt_dlp
import eyed3
from eyed3.id3.frames import ImageFrame
import time
import subprocess
import os
import zipfile
import random

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
SPOTIFY_CLIENT_ID = os.environ.get('SPOTIFY_CLIENT_ID', 'c9d53d6622df48ffbec775e99d16af49')
SPOTIFY_CLIENT_SECRET = os.environ.get('SPOTIFY_CLIENT_SECRET', '35108ddf5b694f118083b5a76fa705bc')
BASE_URL = os.environ.get('BASE_URL', 'https://web-production-7212c.up.railway.app')

spotify = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET
))

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

# Resto de endpoints permanecen igual, solo cambiar URLs hardcodeadas
@app.route('/v1/song')
def songg():
    nombreCancion = request.args.get('name')
    if nombreCancion is None:
        return jsonify({"detail": "Error"}), 400
    
    print(f"nombreCancion: {nombreCancion}")

    try:
        if nombreCancion.startswith("spotify:track:"):
            spotify_song = spotify.track(nombreCancion)
            youtube_song = VideosSearch(spotify_song['name'] + " " + spotify_song['artists'][0]['name'], limit=1).result()
            data = {
                "video_id": youtube_song['result'][0]['id'], 
                "format": "mp3",
                "metadata": {
                    "name": spotify_song['name'], 
                    "release_date": spotify_song['album']['release_date'],
                    "artist": spotify_song['artists'][0]['name'], 
                    "album": spotify_song['album']['name'],
                    "genre": "N/A", 
                    "number": 0,
                    "cover": spotify_song['album']['images'][0]['url'], 
                    "time": spotify_song['duration_ms'],
                    'external_link': spotify_song['external_urls']['spotify']
                },
            }
        else:
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
                    "album": "N/A",
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
        return jsonify({"detail": "Error interno del servidor"}), 500

# [Resto de endpoints permanecen igual - playlist, search/song, checkfiles, zip]
# Solo actualizar las URLs hardcodeadas en handle_message

@socketio.on('message')
def handle_message(message):
    songs_send = []
    print(message['playlist'])
    subprocess.Popen(['spotdl', message['playlist'], '-p', '{title}.{ext}'], 
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    while len(message['songs']) != len(songs_send):
        for song in message['songs']:
            if song["metadata"]["name"] not in songs_send and os.path.isfile(f'{song["metadata"]["name"]}.mp3'):
                try:
                    audiofile = eyed3.load(f'{song["metadata"]["name"]}.mp3')
                    if audiofile.tag is not None and audiofile.tag.title == song["metadata"]["name"]:
                        data = {
                            "song": {"video_id": 0, "format": "mp3"},
                            "metadata": {
                                "name": song['metadata']['name'], 
                                "release_date": song['metadata']['release'],
                                "artist": song['metadata']['artist'], 
                                "album": song['metadata']['album'],
                                "genre": "N/A", 
                                "number": 0,
                                "cover": song['metadata']['cover'], 
                                "time": 0
                            },
                            'position': song['position'],
                            'link': f'{BASE_URL}/v1/file/{song["metadata"]["name"]}.mp3',
                            'external_link': song['metadata']['external_link'],
                            'tamaño': "{} mb".format(
                                round(os.path.getsize(f'{song["metadata"]["name"]}.mp3') / (1024 * 1024), 2))
                        }

                        emit('message_reply', data)
                        songs_send.append(song["metadata"]["name"])
                        socketio.sleep(0.5)
                except Exception as e:
                    print(f"Error procesando canción: {e}")
                    pass
    time.sleep(1)
    emit('disconnect')

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)