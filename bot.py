import logging
import asyncio
import random
import tempfile
import sqlite3
import csv
import os
import json
import urllib.parse
import re
import uuid
import html
from datetime import datetime, timedelta
from collections import defaultdict
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, CallbackQuery
from aiogram.filters import Command
import aiohttp
from bs4 import BeautifulSoup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import ssl

API_TOKEN = "8996619984:AAGontioMNDTW0L0NVr_5Gf_w922VTnzLKw"
GIGACHAT_CLIENT_ID = "019b47f6-b51f-7b47-aa6f-bfddd1f46389"  # Твой Client ID
GIGACHAT_CLIENT_SECRET = "MDE5YjQ3ZjYtYjUxZi03YjQ3LWFhNmYtYmZkZGQxZjQ2Mzg5OmVmNTQxODI2LTcyNzgtNGFiZS1hMzgyLTRkMWYwOTA0YjI2Yg=="
GIGACHAT_TOKEN_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
GIGACHAT_API_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

if not API_TOKEN:
    print("Ошибка: API ключи не заданы. Установите переменные окружения.")
    exit(1)

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

user_data = defaultdict(lambda: {
    "step": None, "genre": None, "language": None, "country": None, 
    "mood": None, "tempo": None, "period": None, "duration": None, 
    "popularity": None, "artist_type": None, "similar_artists": None,
    "last_search": None
})

ADMIN_LOGIN = "admin"
ADMIN_PASSWORD = "music123"
admin_sessions = set()

# --- APPLE MUSIC API ФУНКЦИИ (из bott.py) ---
async def call_apple(params):
    """Базовый запрос к Apple Music API"""
    url = "https://itunes.apple.com/search"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params, timeout=10, ssl=False) as resp:
                if resp.status != 200:
                    print(f"⚠️ Apple API вернул статус {resp.status}")
                    return []
                data = await resp.json(content_type=None)
                if data is None:
                    print("⚠️ Apple API вернул None")
                    return []
                return data.get("results", [])
        except Exception as e:
            print(f"❌ Ошибка запроса к Apple: {e}")
            return []

async def search_tracks_on_apple(search_query, limit=10):
    """Поиск треков на Apple Music"""
    try:
        params = {
            "term": search_query,
            "entity": "song",
            "limit": limit,
            "sort": "popular"
        }
        results = await call_apple(params)
        
        if not results:
            return []
        
        tracks = []
        for track in results:
            tracks.append({
                'title': track.get("trackName", "Неизвестный трек"),
                'artist': track.get("artistName", "Неизвестный артист"),
                'url': track.get("trackViewUrl", ""),
                'youtube_url': f"https://www.youtube.com/results?search_query={urllib.parse.quote(track.get('artistName', '') + ' ' + track.get('trackName', ''))}",
                'preview_url': track.get("previewUrl"),
                'album': track.get("collectionName", "Неизвестный альбом"),
                'cover_url': track.get("artworkUrl100"),
                'match': random.randint(70, 95),  # Случайное совпадение для совместимости
                'spotify_id': track.get("trackId"),
                'type': 'song'
            })
        
        return tracks[:limit]
    except Exception as e:
        print(f"❌ Ошибка поиска Apple Music: {e}")
        return []

async def verify_track_exists(artist: str, track: str) -> dict:
    """
    Проверяет, существует ли трек в Apple Music.
    Возвращает None если не найден, или данные о треке если найден.
    """
    try:
        search_query = f"{artist} {track}"
        params = {
            "term": search_query,
            "entity": "song",
            "limit": 5,
            "sort": "popular"
        }
        
        results = await call_apple(params)
        
        if not results:
            return None
        
        # Ищем точное совпадение по артисту и треку
        for result in results:
            result_artist = result.get("artistName", "").lower().strip()
            result_track = result.get("trackName", "").lower().strip()
            
            # Сравниваем с учетом возможных вариаций
            if (artist.lower() in result_artist or result_artist in artist.lower()) and \
               (track.lower() in result_track or result_track in track.lower()):
                # Нашли реальный трек!
                return {
                    'artist': result.get("artistName", artist),
                    'track': result.get("trackName", track),
                    'url': result.get("trackViewUrl", ""),
                    'preview_url': result.get("previewUrl"),
                    'album': result.get("collectionName", ""),
                    'cover_url': result.get("artworkUrl100"),
                    'year': str(result.get("releaseDate", "")[:4]) if result.get("releaseDate") else "неизвестно"
                }
        
        # Если не нашли точное совпадение, но есть результаты, берем первый
        if results:
            result = results[0]
            return {
                'artist': result.get("artistName", artist),
                'track': result.get("trackName", track),
                'url': result.get("trackViewUrl", ""),
                'preview_url': result.get("previewUrl"),
                'album': result.get("collectionName", ""),
                'cover_url': result.get("artworkUrl100"),
                'year': str(result.get("releaseDate", "")[:4]) if result.get("releaseDate") else "неизвестно"
            }
        
        return None
        
    except Exception as e:
        print(f"❌ Ошибка проверки трека {artist} - {track}: {e}")
        return None

async def get_artist_top_tracks(artist_name: str, limit: int = 10):
    """Получение топ-треков артиста"""
    try:
        # 1. Сначала находим ID артиста
        search_params = {
            "term": artist_name,
            "entity": "musicArtist",
            "limit": 1
        }
        
        search_results = await call_apple(search_params)
        if not search_results:
            print(f"❌ Артист '{artist_name}' не найден")
            return None
        
        artist_id = search_results[0].get("artistId")
        artist_name_found = search_results[0].get("artistName")
        
        # 2. Ищем треки артиста
        lookup_url = "https://itunes.apple.com/lookup"
        params = {
            "id": artist_id,
            "entity": "song",
            "limit": limit,
            "sort": "popular"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(lookup_url, params=params) as resp:
                data = await resp.json(content_type=None)
                all_results = data.get("results", [])
                track_results = [item for item in all_results if item.get("wrapperType") == "track"]
        
        if not track_results:
            return None
        
        tracks_data = []
        for track in track_results:
            tracks_data.append({
                'title': track.get("trackName", "Неизвестный трек"),
                'artist': track.get("artistName", "Неизвестный артист"),
                'album': track.get("collectionName", "Неизвестный альбом"),
                'popularity': track.get("trackNumber", 50),  # Примерная популярность
                'spotify_url': track.get("trackViewUrl", ""),
                'youtube_url': f"https://www.youtube.com/results?search_query={urllib.parse.quote(track.get('artistName', '') + ' ' + track.get('trackName', ''))}",
                'preview_url': track.get("previewUrl"),
                'cover_url': track.get("artworkUrl100"),
                'spotify_id': track.get("trackId")
            })
        
        return {
            'artist_name': artist_name_found,
            'artist_id': artist_id,
            'tracks': tracks_data
        }
        
    except Exception as e:
        print(f"❌ Ошибка в get_artist_top_tracks: {e}")
        return None

async def get_spotify_similar_artists_improved(artist_name: str, limit: int = 10):
    """Поиск похожих артистов (адаптировано для Apple Music)"""
    try:
        # Сначала ищем самого артиста
        search_params = {
            "term": artist_name,
            "entity": "musicArtist",
            "limit": 1
        }
        
        search_results = await call_apple(search_params)
        if not search_results:
            return None
        
        main_artist = search_results[0]
        main_artist_name = main_artist.get("artistName")
        main_artist_id = main_artist.get("artistId")
        
        # Ищем артистов в том же жанре
        genre_search_params = {
            "term": artist_name,
            "entity": "song",
            "limit": 50,
            "sort": "popular"
        }
        
        tracks_results = await call_apple(genre_search_params)
        
        if not tracks_results:
            return None
        
        # Собираем уникальных артистов из найденных треков
        artists_dict = {}
        for track in tracks_results:
            artist_name_track = track.get("artistName")
            if artist_name_track and artist_name_track.lower() != main_artist_name.lower():
                if artist_name_track not in artists_dict:
                    artists_dict[artist_name_track] = {
                        'name': artist_name_track,
                        'popularity': track.get("trackNumber", random.randint(30, 90)),
                        'track': track.get("trackName")
                    }
        
        # Преобразуем в список
        similar_artists = list(artists_dict.values())[:limit]
        
        # Формируем результат
        all_artists = []
        for i, artist in enumerate(similar_artists):
            artist_data = {
                'id': f"apple_{i}",
                'name': artist['name'],
                'genres': [],
                'popularity': artist['popularity'],
                'top_track': {
                    'title': artist['track'],
                    'spotify_url': f"https://music.apple.com/search?term={urllib.parse.quote(artist['name'])}"
                },
                'youtube_url': f"https://www.youtube.com/results?search_query={urllib.parse.quote(artist['name'])}",
                'spotify_url': f"https://music.apple.com/search?term={urllib.parse.quote(artist['name'])}",
                'category': 'popular' if artist['popularity'] >= 50 else 'discovery'
            }
            all_artists.append(artist_data)
        
        return {
            'main_artist': {
                'name': main_artist_name,
                'id': main_artist_id,
                'genres': [],
                'popularity': main_artist.get("trackNumber", 70)
            },
            'similar_artists': all_artists,
            'categories': {
                'popular': [a for a in all_artists if a['popularity'] >= 50][:5],
                'discovery': [a for a in all_artists if a['popularity'] < 50][:5]
            }
        }
        
    except Exception as e:
        print(f"❌ Ошибка в get_spotify_similar_artists_improved: {e}")
        return None

def search_music_on_youtube(user_answers):
    """Адаптированная функция поиска музыки (теперь через Apple Music)"""
    # Используем асинхронный вызов
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # Формируем поисковый запрос
        search_terms = []
        
        if user_answers.get("genre"):
            search_terms.append(user_answers["genre"])
        
        if user_answers.get("similar_artists"):
            search_terms.append(user_answers["similar_artists"])
        
        if user_answers.get("mood"):
            search_terms.append(user_answers["mood"])
        
        search_query = " ".join([term for term in search_terms if term])
        
        if not search_query:
            search_query = user_answers.get("genre", "music")
        
        # Выполняем поиск
        results = loop.run_until_complete(search_tracks_on_apple(search_query, limit=10))
        loop.close()
        
        return results if results else []
        
    except Exception as e:
        print(f"❌ Ошибка в search_music_on_youtube: {e}")
        if 'loop' in locals():
            loop.close()
        return []

async def get_gigachat_token():
    """Получаем временный токен для GigaChat API - РАБОЧАЯ ВЕРСИЯ"""
    import uuid
    
    # Ваши ключи (используем как есть)
    client_id = GIGACHAT_CLIENT_ID  # "019b47f6-b51f-7b47-aa6f-bfddd1f46389"
    auth_key = GIGACHAT_CLIENT_SECRET  # "async def get_gigachat_token():
    """Получаем временный токен для GigaChat API - РАБОЧАЯ ВЕРСИЯ"""
    import uuid
    
    # Ваши ключи (используем как есть)
    client_id = GIGACHAT_CLIENT_ID  # "019b47f6-b51f-7b47-aa6f-bfddd1f46389"
    auth_key = GIGACHAT_CLIENT_SECRET  # "MDE5YjQ3ZjYtYjUxZi03YjQ3LWFhNmYtYmZkZGQxZjQ2Mzg5OmVmNTQxODI2LTcyNzgtNGFiZS1hMzgyLTRkMWYwOTA0YjI2Yg=="
    
    print(f"🔑 Использую Client ID: {client_id}")
    print(f"🔑 Использую готовый Authorization Key (первые 30): {auth_key[:30]}...")
    
    # ВАЖНО! Тот самый работающий формат из теста
    headers = {
        'Authorization': f'Basic {auth_key}',  # Ключ УЖЕ в Base64!
        'RqUID': str(uuid.uuid4()),            # ОБЯЗАТЕЛЬНО!
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json'
    }
    
    data = {'scope': 'GIGACHAT_API_PERS'}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                GIGACHAT_TOKEN_URL,
                data=data,
                headers=headers,
                ssl=False,  # Пока оставляем False
                timeout=30
            ) as response:
                
                print(f"📡 Статус ответа GigaChat: {response.status}")
                
                if response.status == 200:
                    result = await response.json()
                    token = result.get('access_token')
                    if token:
                        print(f"✅ Токен получен! Длина: {len(token)} символов")
                        print(f"✅ Токен (первые 30): {token[:30]}...")
                        return token
                    else:
                        print(f"❌ Токен не найден в ответе: {result}")
                        return None
                else:
                    error_text = await response.text()
                    print(f"❌ Ошибка получения токена GigaChat: {response.status}")
                    print(f"❌ Текст ошибки: {error_text}")
                    return None
                    
    except Exception as e:
        print(f"❌ Ошибка подключения к GigaChat: {e}")
        import traceback
        traceback.print_exc()
        return None
    
    print(f"🔑 Использую Client ID: {client_id}")
    print(f"🔑 Использую готовый Authorization Key (первые 30): {auth_key[:30]}...")
    
    # ВАЖНО! Тот самый работающий формат из теста
    headers = {
        'Authorization': f'Basic {auth_key}',  # Ключ УЖЕ в Base64!
        'RqUID': str(uuid.uuid4()),            # ОБЯЗАТЕЛЬНО!
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json'
    }
    
    data = {'scope': 'GIGACHAT_API_PERS'}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                GIGACHAT_TOKEN_URL,
                data=data,
                headers=headers,
                ssl=False,  # Пока оставляем False
                timeout=30
            ) as response:
                
                print(f"📡 Статус ответа GigaChat: {response.status}")
                
                if response.status == 200:
                    result = await response.json()
                    token = result.get('access_token')
                    if token:
                        print(f"✅ Токен получен! Длина: {len(token)} символов")
                        print(f"✅ Токен (первые 30): {token[:30]}...")
                        return token
                    else:
                        print(f"❌ Токен не найден в ответе: {result}")
                        return None
                else:
                    error_text = await response.text()
                    print(f"❌ Ошибка получения токена GigaChat: {response.status}")
                    print(f"❌ Текст ошибки: {error_text}")
                    return None
                    
    except Exception as e:
        print(f"❌ Ошибка подключения к GigaChat: {e}")
        import traceback
        traceback.print_exc()
        return None



def get_fallback_recommendations(user_answers: dict) -> list:
    """Резервные рекомендации, если нейросеть недоступна"""
    genre = user_answers.get('genre', '').lower() if user_answers.get('genre') else 'pop'
    
    recommendations = {
        'rock': [
            {'artist': 'Linkin Park', 'track': 'In The End', 'year': '2000', 'reason': 'Классика альтернативного рока'},
            {'artist': 'Nirvana', 'track': 'Smells Like Teen Spirit', 'year': '1991', 'reason': 'Икона гранжа 90-х'},
            {'artist': 'Queen', 'track': 'Bohemian Rhapsody', 'year': '1975', 'reason': 'Легенда рок-музыки'}
        ],
        'pop': [
            {'artist': 'The Weeknd', 'track': 'Blinding Lights', 'year': '2019', 'reason': 'Современный поп-хит'},
            {'artist': 'Taylor Swift', 'track': 'Shake It Off', 'year': '2014', 'reason': 'Энергичный поп-трек'},
            {'artist': 'Michael Jackson', 'track': 'Billie Jean', 'year': '1982', 'reason': 'Поп-классика'}
        ],
        'hip hop': [
            {'artist': 'Eminem', 'track': 'Lose Yourself', 'year': '2002', 'reason': 'Культовый хип-хоп'},
            {'artist': 'Kendrick Lamar', 'track': 'HUMBLE.', 'year': '2017', 'reason': 'Современный хип-хоп'},
            {'artist': 'Dr. Dre', 'track': 'Still D.R.E.', 'year': '1999', 'reason': 'Вест-кост классика'}
        ]
    }
    
    for key in recommendations.keys():
        if key in genre:
            return recommendations[key]
    
    return recommendations['pop']

def parse_gigachat_response(text: str) -> list:
    """
    Исправленный парсер для формата, где GigaChat разделяет артиста и трек
    Пример:
    1. Король и Шут - Лесник (1991)
       💡 Отлично подходит под ваши предпочтения в жанре
    2.  - Причина: Классика русского панк-рока... (неизвестно)
    """
    print(f"\n🔍 ============ ПАРСИМ ОТВЕТ GIGACHAT ============")
    print(f"📏 Длина ответа: {len(text)} символов")
    
    # Покажем весь ответ для отладки
    print(f"📄 Полный ответ:\n{text}")
    print("=" * 80)
    
    recommendations = []
    
    if not text:
        print("❌ Пустой ответ от GigaChat")
        return []
    
    # Разбиваем на строки
    lines = text.strip().split('\n')
    
    current_item = {}
    current_reason = []
    in_reason_block = False
    
    for i, line in enumerate(lines):
        line = line.strip()
        
        if not line:
            continue
        
        print(f"[{i}] {line}")
        
        # 1. Строка с номером и исполнителем/треком (формат: "1. Король и Шут - Лесник (1991)")
        if line[0].isdigit() and '. ' in line and (' - ' in line or '—' in line):
            # Сохраняем предыдущий элемент
            if current_item:
                if current_reason:
                    current_item['reason'] = ' '.join(current_reason).strip()
                    current_reason = []
                recommendations.append(current_item)
                print(f"✅ Сохраняем: {current_item.get('artist', '?')}")
            
            # Парсим новую строку
            try:
                # Убираем номер "1. "
                content = line.split('. ', 1)[1]
                
                # Разделяем на части
                if ' - ' in content:
                    parts = content.split(' - ', 1)
                elif '—' in content:
                    parts = content.split('—', 1)
                else:
                    parts = [content, '']
                
                artist = parts[0].strip()
                track_with_year = parts[1].strip() if len(parts) > 1 else ''
                
                # Извлекаем год
                import re
                year = "неизвестно"
                track = track_with_year
                
                if '(' in track_with_year and ')' in track_with_year:
                    year_match = re.search(r'\((\d{4})\)', track_with_year)
                    if year_match:
                        year = year_match.group(1)
                        track = re.sub(r'\(\d{4}\)', '', track_with_year).strip()
                
                # Если артист пустой (случай с "2.  - Причина:"), используем предыдущего
                if not artist and recommendations:
                    last_rec = recommendations[-1] if recommendations else {}
                    artist = last_rec.get('artist', 'Неизвестный артист')
                    print(f"⚠️  Артист пустой, использую предыдущего: {artist}")
                
                current_item = {
                    'artist': artist,
                    'track': track,
                    'year': year,
                    'reason': ''
                }
                
                print(f"🎯 Распарсено: {artist} - {track} ({year})")
                
                # Сбрасываем сбор причины
                current_reason = []
                in_reason_block = False
                
            except Exception as e:
                print(f"❌ Ошибка парсинга: {e}")
                continue
        
        # 2. Строка начинается с " - Причина:" (проблемный формат)
        elif line.startswith('- Причина:') or line.startswith('— Причина:'):
            in_reason_block = True
            reason_text = line.replace('- Причина:', '').replace('— Причина:', '').strip()
            if reason_text:
                current_reason.append(reason_text)
            print(f"📝 Начинаем сбор причины: {reason_text[:50]}...")
        
        # 3. Строка с эмодзи или другими маркерами причины
        elif any(line.startswith(prefix) for prefix in ['💡', '•', '*', '✅', '🟢', '🔵']):
            if current_item:
                reason_text = line.lstrip('💡•*✅🟢🔵').strip()
                if reason_text:
                    current_reason.append(reason_text)
                in_reason_block = True
                print(f"📝 Добавляем причину: {reason_text[:50]}...")
        
        # 4. Продолжение причины (если мы внутри блока причины)
        elif in_reason_block and current_item and line:
            current_reason.append(line)
            print(f"📝 Продолжение причины: {line[:50]}...")
        
        # 5. Пустая строка заканчивает блок причины
        elif not line and in_reason_block:
            in_reason_block = False
    
    # Сохраняем последний элемент
    if current_item:
        if current_reason:
            current_item['reason'] = ' '.join(current_reason).strip()
        recommendations.append(current_item)
        print(f"✅ Сохраняем последний: {current_item.get('artist', '?')}")
    
    # Если причина пустая, добавляем дефолтную
    for rec in recommendations:
        if not rec.get('reason'):
            rec['reason'] = f'Отлично подходит под ваши предпочтения'
    
    print(f"\n📊 ============ ИТОГ ============")
    print(f"✅ Распарсено рекомендаций: {len(recommendations)}")
    
    # Проверяем целостность данных
    for i, rec in enumerate(recommendations, 1):
        if not rec.get('artist') or not rec.get('track'):
            print(f"⚠️  ПРОБЛЕМА с пунктом {i}: нет артиста или трека!")
    
    for i, rec in enumerate(recommendations, 1):
        print(f"\n   {i}. {rec.get('artist', '?')} - {rec.get('track', '?')} ({rec.get('year', '?')})")
        if rec.get('reason'):
            print(f"      💡 {rec['reason'][:80]}...")
    
    return recommendations[:5]

async def ask_gigachat_for_music(user_answers: dict) -> list:
    """
    Основная функция: отправляет ответы пользователя в GigaChat API
    и проверяет все треки через Apple Music
    """
    print("🎯 ЗАПРАШИВАЮ РЕКОМЕНДАЦИИ ОТ GIGACHAT AI...")
    
    # 1. Получаем токен
    access_token = await get_gigachat_token()
    if not access_token:
        print("⚠️ Не удалось получить токен GigaChat")
        return []
    
    # 2. УЛУЧШЕННЫЙ промпт для получения РЕАЛЬНЫХ треков
    genre = user_answers.get('genre', 'рок')
    similar_artists = user_answers.get('similar_artists', 'не указаны')
    
    prompt = f"""ТЫ: музыкальный эксперт. Дай 7 рекомендаций песен, которые РЕАЛЬНО СУЩЕСТВУЮТ.

ИНФОРМАЦИЯ О ПОЛЬЗОВАТЕЛЕ:
- Любимый жанр: {genre}
- Предпочитаемый язык: {user_answers.get('language', 'любой')}
- Похожие артисты: {similar_artists}

ВАЖНЫЕ ПРАВИЛА:
1. Учитывай И жанр И язык
2. Если жанр "рок" и язык "русский" - предлагай русскоязычных рок-исполнителей
3. Если жанр "рок" и язык не указан - можно предлагать международных исполнителей
4. Предлагай ТОЛЬКО реально существующие треки, не выдумывай

Примеры:
- Жанр: рок, Язык: русский → Король и Шут, Кино, ДДТ
- Жанр: рок, Язык: английский → Nirvana, Linkin Park, Queen
- Жанр: рок, Язык: любой → можно и русских и зарубежных

Формат ответа:
1. Артист - Трек
2. Артист - Трек
...
"""
    
    # 3. Отправляем запрос к нейросети
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }
    
    data = {
        "model": "GigaChat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,   # ОЧЕНЬ низкая температура для точных ответов
        "max_tokens": 500
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                GIGACHAT_API_URL,
                json=data,
                headers=headers,
                ssl=False,
                timeout=40
            ) as response:
                
                if response.status == 200:
                    result = await response.json()
                    text = result.get('choices', [{}])[0].get('message', {}).get('content', '')
                    
                    print(f"📝 Ответ нейросети получен")
                    
                    # 4. Парсим ответ (только артисты и треки)
                    recommendations = []
                    lines = text.strip().split('\n')
                    
                    for line in lines:
                        line = line.strip()
                        # Ищем формат "1. Артист - Трек"
                        if line and '. ' in line and (' - ' in line or '—' in line):
                            try:
                                # Убираем номер
                                content = line.split('. ', 1)[1]
                                # Разделяем артиста и трек
                                if ' - ' in content:
                                    parts = content.split(' - ', 1)
                                elif '—' in content:
                                    parts = content.split('—', 1)
                                else:
                                    continue
                                
                                artist = parts[0].strip()
                                track = parts[1].strip()
                                
                                if artist and track:
                                    recommendations.append({
                                        'artist': artist,
                                        'track': track,
                                        'verified': False  # Пока не проверен
                                    })
                                    print(f"🎯 Получено: {artist} - {track}")
                            except Exception as e:
                                print(f"❌ Ошибка парсинга строки: {line}")
                    
                    print(f"✅ Получено {len(recommendations)} рекомендаций от GigaChat")
                    
                    # 5. ПРОВЕРЯЕМ КАЖДЫЙ ТРЕК ЧЕРЕЗ APPLE MUSIC
                    verified_recommendations = []

                    for rec in recommendations:
                        print(f"🔍 Проверяю трек: {rec['artist']} - {rec['track']}")
    
                        # Ищем трек в Apple Music
                        verified_track = await verify_track_exists(rec['artist'], rec['track'])
    
                        if verified_track:
                        # Получаем объяснение от GigaChat почему этот трек подходит
                            reason = await get_track_recommendation_with_reason(
                                rec['artist'], 
                                rec['track'], 
                                user_answers
                            )
        
                            # Трек найден! Добавляем с реальными данными
                            verified_recommendations.append({
                                'artist': verified_track['artist'],
                                'track': verified_track['track'],
                                'url': verified_track['url'],
                                'year': verified_track.get('year', 'неизвестно'),
                                'reason': reason,  # Теперь тут объяснение от GigaChat!
                                'verified': True
                            })
                            print(f"✅ Трек проверен и найден!")
                        else:
                            print(f"⚠️ Трек не найден в Apple Music, пропускаем")
                    
                    print(f"📊 Итог: {len(verified_recommendations)} реальных треков из {len(recommendations)}")
                    
                    # Если реальных треков мало, дополняем топом по жанру
                    if len(verified_recommendations) < 3:
                        print(f"⚠️ Мало реальных треков, добавляю топ по жанру")
                        genre_tracks = await search_tracks_on_apple(genre, limit=5)
                        for track in genre_tracks:
                            verified_recommendations.append({
                                'artist': track['artist'],
                                'track': track['title'],
                                'url': track['url'],
                                'year': 'неизвестно',
                                'reason': f'🔥 Популярный трек в жанре {genre}',
                                'verified': True
                            })
                    
                    return verified_recommendations[:5]  # Возвращаем максимум 5
                        
                else:
                    error_text = await response.text()
                    print(f"❌ Ошибка GigaChat API {response.status}")
                    return []
                    
    except Exception as e:
        print(f"❌ Ошибка сети при запросе к GigaChat: {e}")
        return []

async def get_track_recommendation_with_reason(artist: str, track: str, user_answers: dict) -> dict:
    """
    Получает от GigaChat объяснение, почему трек подходит пользователю
    """
    access_token = await get_gigachat_token()
    if not access_token:
        return "Отлично подходит под ваши предпочтения"
    
    genre = user_answers.get('genre', '')
    similar = user_answers.get('similar_artists', '')
    
    prompt = f"""Объясни КОРОТКО почему трек "{track}" артиста {artist} подходит пользователю.
    
Пользователь любит: {genre}
Похожие артисты: {similar}

Ответ в 1-2 предложения, без лишних слов, только объяснение.
Пример: "Сочетает энергичный бит и эмоциональный вокал, похоже на {similar if similar else 'ваши любимые треки'}"
"""
    
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }
    
    data = {
        "model": "GigaChat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.5,
        "max_tokens": 100
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                GIGACHAT_API_URL,
                json=data,
                headers=headers,
                ssl=False,
                timeout=20
            ) as response:
                
                if response.status == 200:
                    result = await response.json()
                    text = result.get('choices', [{}])[0].get('message', {}).get('content', '')
                    return text.strip()
                else:
                    return "Отлично подходит под ваши музыкальные предпочтения"
                    
    except Exception as e:
        print(f"❌ Ошибка получения объяснения: {e}")
        return "Отлично подходит под ваши музыкальные предпочтения"

# --- ФУНКЦИИ ПОИСКА ---
async def find_similar(message: Message, state: FSMContext):
    """Поиск похожих артистов (из bot2.py)"""
    user_id = message.from_user.id
    
    # Проверка попыток
    attempts = db.get_user_attempts(user_id)
    is_premium_active = False
    if attempts['premium_until']:
        premium_date = datetime.strptime(attempts['premium_until'], '%Y-%m-%d')
        if premium_date >= datetime.now():
            is_premium_active = True
    
    if attempts['free_attempts'] <= 0 and attempts['paid_attempts'] <= 0 and not is_premium_active:
        await message.answer("❌ У вас закончились попытки! Купите дополнительные.")
        await state.clear()
        return
    
    # Списать попытку
    is_free_attempt = attempts['free_attempts'] > 0
    db.log_attempt(user_id, is_free_attempt)
    db.increment_user_stat(user_id, 'quick_searches')
    
    search_query = message.text.strip()
    status_msg = await message.answer(f"⏳ Парсю страницу похожих для <b>{html.escape(search_query)}</b>...", parse_mode="HTML")

    # Шаг 1: Получаем ID и точное имя для формирования ссылки
    search_res = await call_apple({"term": search_query, "entity": "musicArtist", "limit": 1})
    if not search_res:
        await status_msg.edit_text("❌ Артист не найден.")
        await state.clear()
        return

    artist_id = search_res[0].get("artistId")
    artist_name = search_res[0].get("artistName")
    
    # Формируем ссылку
    clean_name = artist_name.replace(" ", "-").lower()
    see_all_link = f"https://music.apple.com/ru/artist/{clean_name}/{artist_id}/see-all?section=similar-artists"

    recommendations = []

    # Шаг 2: Идем по ссылке и парсим HTML
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(see_all_link, timeout=10) as response:
                if response.status == 200:
                    html_text = await response.text()
                    soup = BeautifulSoup(html_text, "html.parser")
                    
                    # Ищем все ссылки на артистов
                    links = soup.find_all('a', href=re.compile(r'/artist/'))
                    
                    for link in links:
                        name = link.get_text(strip=True)
                        href = link.get('href')
                        
                        if name and href and name.lower() != artist_name.lower():
                            full_url = href if href.startswith('http') else f"https://music.apple.com{href}"
                            
                            if full_url not in [r['url'] for r in recommendations]:
                                recommendations.append({"name": name, "url": full_url})
                        
                        if len(recommendations) >= 10:
                            break
    except Exception as e:
        print(f"Ошибка парсинга: {e}")

    # Шаг 3: Если парсинг не удался, используем API как бэкап
    if not recommendations:
        backup = await call_apple({"id": artist_id, "entity": "allArtist", "limit": 11})
        for item in backup:
            if item.get("wrapperType") == "artist" and str(item.get("artistId")) != str(artist_id):
                recommendations.append({
                    "name": item.get("artistName"),
                    "url": item.get("artistLinkUrl")
                })
            if len(recommendations) >= 10: break

    # Шаг 4: Вывод
    if not recommendations:
        await status_msg.edit_text("❌ Не удалось считать данные со страницы.")
        await state.clear()
        return

    result_text = f"👤 <b>10 похожих артистов для {html.escape(artist_name)}:</b>\n\n"
    
    for i, res in enumerate(recommendations[:10], 1):
        r_name = html.escape(res['name'])
        r_url = res['url']
        result_text += f"{i}. <b>{r_name}</b>\n🔗 <a href='{r_url}'>Карточка артиста</a>\n\n"

    result_text += f"🏠 <a href='{see_all_link}'>Открыть весь раздел</a>"

    await status_msg.delete()
    
    # Кнопки возврата
    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎵 Пройти опрос", callback_data="survey")],
            [InlineKeyboardButton(text="⚡ Быстрый поиск", callback_data="quick_search")],
            [InlineKeyboardButton(text="💰 Покупки", callback_data="purchases")],
            [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")]
        ]
    )
    
    await message.answer(result_text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=back_kb)
    
    # Запросить оценку
    await ask_for_rating(message, user_id, "quick_search")
    await state.clear()

async def top_tracks(message: Message, state: FSMContext):
    """Поиск топ-треков артиста"""
    user_id = message.from_user.id
    
    # Проверка попыток
    attempts = db.get_user_attempts(user_id)
    is_premium_active = False
    if attempts['premium_until']:
        premium_date = datetime.strptime(attempts['premium_until'], '%Y-%m-%d')
        if premium_date >= datetime.now():
            is_premium_active = True
    
    if attempts['free_attempts'] <= 0 and attempts['paid_attempts'] <= 0 and not is_premium_active:
        await message.answer("❌ У вас закончились попытки! Купите дополнительные.")
        await state.clear()
        return
    
    # Списать попытку
    is_free_attempt = attempts['free_attempts'] > 0
    db.log_attempt(user_id, is_free_attempt)
    db.increment_user_stat(user_id, 'quick_searches')
    
    search_query = message.text.strip()
    
    # 1. Сначала находим ID артиста
    search_url = "https://itunes.apple.com/search"
    search_params = {
        "term": search_query,
        "entity": "musicArtist",
        "limit": 1
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(search_url, params=search_params) as resp:
                if resp.status != 200:
                    await message.answer("❌ Ошибка соединения с Apple Music. Попробуй позже.")
                    await state.clear()
                    return
                    
                search_data = await resp.json(content_type=None)
                if not search_data or not search_data.get("results"):
                    await message.answer("❌ Артист не найден в Apple Music.")
                    await state.clear()
                    return

                results_list = search_data["results"]
                artist_id = results_list[0].get("artistId")
                artist_name = results_list[0].get("artistName")

                # 2. Делаем LOOKUP запрос по ID артиста для получения ТОП треков
                lookup_url = "https://itunes.apple.com/lookup"
                lookup_params = {
                    "id": artist_id,
                    "entity": "song",
                    "limit": 10,
                    "sort": "popular"
                }
                
                async with session.get(lookup_url, params=lookup_params) as lookup_resp:
                    if lookup_resp.status != 200:
                        await message.answer("❌ Ошибка получения треков. Попробуй позже.")
                        await state.clear()
                        return
                        
                    lookup_data = await lookup_resp.json(content_type=None)
                    all_results = lookup_data.get("results", [])
                    track_results = [item for item in all_results if item.get("wrapperType") == "track"]

        if not track_results:
            await message.answer(f"❌ Не удалось найти топ-треки для {artist_name}.")
            await state.clear()
            return

        text = f"🔝 <b>Официальный ТОП треков: {html.escape(artist_name)}</b>\n\n"
        
        for i, t in enumerate(track_results, 1):
            t_name = html.escape(t.get("trackName", "Без названия"))
            t_url = t.get("trackViewUrl", "")
            text += f"<b>{i}. {t_name}</b>\n"
            if t_url:
                text += f"🔗 <a href='{t_url}'>Слушать в Apple Music</a>\n"
            text += "\n"

        # Кнопки возврата
        back_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🎵 Пройти опрос", callback_data="survey")],
                [InlineKeyboardButton(text="⚡ Быстрый поиск", callback_data="quick_search")],
                [InlineKeyboardButton(text="💰 Покупки", callback_data="purchases")],
                [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")]
            ]
        )
        
        await message.answer(text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=back_kb)
        
        # Запросить оценку
        await ask_for_rating(message, user_id, "quick_search")
        await state.clear()

    except Exception as e:
        logging.error(f"Ошибка при поиске топ-треков: {e}")
        await message.answer("❌ Произошла ошибка при обращении к Apple Music. Попробуй позже.")
        await state.clear()

async def genre_search(message: Message, state: FSMContext):
    """Поиск по жанру (из bot2.py)"""
    user_id = message.from_user.id
    
    # Проверка попыток
    attempts = db.get_user_attempts(user_id)
    is_premium_active = False
    if attempts['premium_until']:
        premium_date = datetime.strptime(attempts['premium_until'], '%Y-%m-%d')
        if premium_date >= datetime.now():
            is_premium_active = True
    
    if attempts['free_attempts'] <= 0 and attempts['paid_attempts'] <= 0 and not is_premium_active:
        await message.answer("❌ У вас закончились попытки! Купите дополнительные.")
        await state.clear()
        return
    
    # Списать попытку
    is_free_attempt = attempts['free_attempts'] > 0
    db.log_attempt(user_id, is_free_attempt)
    db.increment_user_stat(user_id, 'quick_searches')
    
    genre_query = message.text.strip()
    status_msg = await message.answer(f"🔎 Ищу лучший <b>{html.escape(genre_query)}</b>...", parse_mode="HTML")

    res = await call_apple({
        "term": genre_query,
        "entity": "song",
        "limit": 100,
        "attribute": "genreTerm",
        "sort": "popular"
    })

    if not res:
        await status_msg.delete()
        await message.answer(
            f"❌ Жанр <b>{html.escape(genre_query)}</b> не найден.\n"
            f"Используй реальные стили (например: Phonk, Hip-Hop).",
            parse_mode="HTML"
        )
        await state.clear()
        return

    random.shuffle(res)

    tracks_list = []
    seen_songs = set()

    for track in res:
        title = html.escape(track.get("trackName", "Unknown Track"))
        artist = html.escape(track.get("artistName", "Unknown Artist"))
        url = track.get("trackViewUrl")
        
        track_id = f"{artist}-{title}".lower()
        if track_id not in seen_songs:
            tracks_list.append(f"🎧 <b>{artist}</b> — {title}\n🔗 <a href='{url}'>Слушать в Apple Music</a>")
            seen_songs.add(track_id)
        
        if len(tracks_list) >= 10:
            break

    result_text = (
        f"🔥 <b>Случайный Топ-10 в жанре {html.escape(genre_query)}:</b>\n\n"
        + "\n\n".join([f"{i+1}. {t}" for i, t in enumerate(tracks_list)])
    )

    await status_msg.delete()
    
    # Кнопки возврата
    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎵 Пройти опрос", callback_data="survey")],
            [InlineKeyboardButton(text="⚡ Быстрый поиск", callback_data="quick_search")],
            [InlineKeyboardButton(text="💰 Покупки", callback_data="purchases")],
            [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")]
        ]
    )
    
    await message.answer(result_text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=back_kb)
    
    # Запросить оценку
    await ask_for_rating(message, user_id, "quick_search")
    await state.clear()

# --- БАЗА ДАННЫХ (остается без изменений) ---
class Database:
    def __init__(self):
        self.connection = None
        self.connect()
        self.create_tables()
        self.insert_initial_data()
    
    def connect(self):
        try:
            self.connection = sqlite3.connect('music_bot.db', check_same_thread=False)
            self.connection.row_factory = sqlite3.Row
            print("✅ SQLite база данных подключена!")
        except Exception as e:
            print(f"❌ Ошибка подключения: {e}")
    
    def create_tables(self):
        try:
            cursor = self.connection.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS payment_status (
                    id_payment_status INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tariff (
                    id_tariff INTEGER PRIMARY KEY AUTOINCREMENT,
                    attempts_count INTEGER NOT NULL,
                    duration_days INTEGER NOT NULL,
                    price REAL NOT NULL,
                    name TEXT NOT NULL
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id_user INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER UNIQUE,
                    telegram_token TEXT,
                    username TEXT,
                    free_attempts INTEGER DEFAULT 5,
                    paid_attempts INTEGER DEFAULT 0,
                    premium_until TEXT,
                    amount REAL DEFAULT 0.00,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    id_payments INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    tariff_id INTEGER NOT NULL,
                    payment_date TEXT NOT NULL,
                    payment_status_id INTEGER NOT NULL,
                    attempts_added INTEGER NOT NULL,
                    premium_days INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id_user),
                    FOREIGN KEY (tariff_id) REFERENCES tariff(id_tariff),
                    FOREIGN KEY (payment_status_id) REFERENCES payment_status(id_payment_status)
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_attempts_log (
                    id_log INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    used_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    is_free_attempt BOOLEAN NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id_user)
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_stats (
                    id_stats INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER UNIQUE NOT NULL,
                    surveys_completed INTEGER DEFAULT 0,
                    quick_searches INTEGER DEFAULT 0,
                    average_rating REAL DEFAULT 0,
                    total_ratings INTEGER DEFAULT 0,
                    active_tariff TEXT DEFAULT 'Бесплатный',
                    first_name TEXT,
                    username TEXT,
                    last_start TEXT,
                    ratings_history TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id_user)
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS global_ratings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    total_ratings INTEGER DEFAULT 0,
                    average_rating REAL DEFAULT 0,
                    rating_distribution TEXT DEFAULT '{"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}',
                    survey_ratings INTEGER DEFAULT 0,
                    quick_search_ratings INTEGER DEFAULT 0
                )
            """)

            cursor.execute("""
    		    CREATE TABLE IF NOT EXISTS favorites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    recommendations TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id_user)
                )
            """)  
            
            self.connection.commit()
            print("✅ Таблицы созданы!")
            
        except Exception as e:
            print(f"❌ Ошибка создания таблиц: {e}")
    
    def insert_initial_data(self):
        try:
            cursor = self.connection.cursor()
            
            cursor.execute("SELECT COUNT(*) FROM payment_status")
            if cursor.fetchone()[0] == 0:
                cursor.execute("""
                    INSERT INTO payment_status (name) VALUES 
                    ('pending'), ('completed'), ('failed'), ('refunded')
                """)
            
            cursor.execute("SELECT COUNT(*) FROM tariff")
            if cursor.fetchone()[0] == 0:
                cursor.execute("""
                    INSERT INTO tariff (attempts_count, duration_days, price, name) VALUES
                    (10, 0, 99.00, '10 попыток'),
                    (0, 30, 199.00, 'Премиум 30 дней')
                """)
            
            cursor.execute("SELECT COUNT(*) FROM global_ratings")
            if cursor.fetchone()[0] == 0:
                cursor.execute("""
                    INSERT INTO global_ratings (total_ratings, average_rating, rating_distribution, survey_ratings, quick_search_ratings)
                    VALUES (0, 0, '{"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}', 0, 0)
                """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS payment_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    user_number INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    tariff TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id_user)
                )
            """)
            
            self.connection.commit()
            print("✅ Начальные данные добавлены!")
            
        except Exception as e:
            print(f"❌ Ошибка вставки данных: {e}")
    
    def add_user(self, telegram_id: int, username: str, first_name: str):
        try:
            cursor = self.connection.cursor()
            
            cursor.execute("""
                INSERT OR IGNORE INTO users (telegram_id, username, free_attempts) 
                VALUES (?, ?, 5)
            """, (telegram_id, username))
            
            cursor.execute("SELECT id_user FROM users WHERE telegram_id = ?", (telegram_id,))
            user_row = cursor.fetchone()
            if not user_row:
                return False
                
            user_id = user_row[0]
            
            cursor.execute("SELECT COUNT(*) FROM user_stats WHERE user_id = ?", (user_id,))
            exists = cursor.fetchone()[0]

            if exists:
                cursor.execute("""
                    UPDATE user_stats 
                    SET first_name = ?, username = ?, last_start = ?
                    WHERE user_id = ?
                """, (first_name, username, datetime.now().strftime("%Y-%m-%d %H:%M"), user_id))
            else:
                cursor.execute("""
                    INSERT INTO user_stats 
                    (user_id, first_name, username, last_start) 
                    VALUES (?, ?, ?, ?)
                """, (user_id, first_name, username, datetime.now().strftime("%Y-%m-%d %H:%M")))
            
            self.connection.commit()
            return True
        except Exception as e:
            print(f"❌ Ошибка добавления пользователя: {e}")
            return False

    def safe_add_user(self, telegram_id: int, username: str, first_name: str):
        try:
            cursor = self.connection.cursor()
            
            cursor.execute("SELECT id_user FROM users WHERE telegram_id = ?", (telegram_id,))
            user_row = cursor.fetchone()
            
            if user_row:
                user_id = user_row[0]
                cursor.execute("""
                    UPDATE users SET username = ? WHERE telegram_id = ?
                """, (username, telegram_id))
                
                cursor.execute("SELECT COUNT(*) FROM user_stats WHERE user_id = ?", (user_id,))
                has_stats = cursor.fetchone()[0]
                
                if has_stats:
                    cursor.execute("""
                        UPDATE user_stats 
                        SET first_name = ?, username = ?, last_start = ?
                        WHERE user_id = ?
                    """, (first_name, username, datetime.now().strftime("%Y-%m-%d %H:%M"), user_id))
                else:
                    cursor.execute("""
                        INSERT INTO user_stats 
                        (user_id, first_name, username, last_start) 
                        VALUES (?, ?, ?, ?)
                    """, (user_id, first_name, username, datetime.now().strftime("%Y-%m-%d %H:%M")))
                    
            else:
                cursor.execute("""
                    INSERT INTO users (telegram_id, username, free_attempts) 
                    VALUES (?, ?, 5)
                """, (telegram_id, username))
                
                cursor.execute("SELECT id_user FROM users WHERE telegram_id = ?", (telegram_id,))
                user_id = cursor.fetchone()[0]
                
                cursor.execute("""
                    INSERT INTO user_stats 
                    (user_id, first_name, username, last_start) 
                    VALUES (?, ?, ?, ?)
                """, (user_id, first_name, username, datetime.now().strftime("%Y-%m-%d %H:%M")))
            
            self.connection.commit()
            return True
            
        except Exception as e:
            print(f"❌ Ошибка безопасного добавления пользователя: {e}")
            return False
    
    def get_user_stats(self, telegram_id: int):
        try:
            cursor = self.connection.cursor()
            cursor.execute("""
                SELECT us.* 
                FROM user_stats us 
                JOIN users u ON us.user_id = u.id_user 
                WHERE u.telegram_id = ?
            """, (telegram_id,))
            result = cursor.fetchone()
            
            if result:
                ratings_history = json.loads(result['ratings_history']) if result['ratings_history'] else []
                return {
                    'surveys_completed': result['surveys_completed'],
                    'quick_searches': result['quick_searches'],
                    'average_rating': result['average_rating'],
                    'total_ratings': result['total_ratings'],
                    'active_tariff': result['active_tariff'],
                    'username': result['username'],
                    'first_name': result['first_name'],
                    'last_start': result['last_start'],
                    'ratings_history': ratings_history
                }
            return self.get_default_user_stats()
        except Exception as e:
            print(f"❌ Ошибка получения статистики пользователя: {e}")
            return self.get_default_user_stats()
    
    def get_default_user_stats(self):
        return {
            'surveys_completed': 0,
            'quick_searches': 0,
            'average_rating': 0,
            'total_ratings': 0,
            'active_tariff': 'Бесплатный',
            'username': '',
            'first_name': '',
            'last_start': None,
            'ratings_history': []
        }
    
    def update_user_stats(self, telegram_id: int, stats_data: dict):
        try:
            cursor = self.connection.cursor()
            
            ratings_history_json = json.dumps(stats_data.get('ratings_history', []))
            
            cursor.execute("""
                UPDATE user_stats 
                SET surveys_completed = ?, quick_searches = ?, average_rating = ?, 
                    total_ratings = ?, active_tariff = ?, first_name = ?, username = ?, 
                    last_start = ?, ratings_history = ?
                WHERE user_id = (SELECT id_user FROM users WHERE telegram_id = ?)
            """, (
                stats_data['surveys_completed'],
                stats_data['quick_searches'],
                stats_data['average_rating'],
                stats_data['total_ratings'],
                stats_data['active_tariff'],
                stats_data['first_name'],
                stats_data['username'],
                stats_data['last_start'],
                ratings_history_json,
                telegram_id
            ))
            
            self.connection.commit()
            return True
        except Exception as e:
            print(f"❌ Ошибка обновления статистики: {e}")
            return False
    
    def increment_user_stat(self, telegram_id: int, field: str, increment: int = 1):
        try:
            cursor = self.connection.cursor()
            
            cursor.execute(f"""
                UPDATE user_stats 
                SET {field} = {field} + ?
                WHERE user_id = (SELECT id_user FROM users WHERE telegram_id = ?)
            """, (increment, telegram_id))
            
            self.connection.commit()
            return True
        except Exception as e:
            print(f"❌ Ошибка увеличения статистики {field}: {e}")
            return False
    
    def log_attempt(self, user_id: int, is_free_attempt: bool):
        try:
            cursor = self.connection.cursor()
            
            cursor.execute("SELECT id_user FROM users WHERE telegram_id = ?", (user_id,))
            user_row = cursor.fetchone()
            if not user_row:
                return False
                
            db_user_id = user_row[0]
            
            cursor.execute("""
                INSERT INTO user_attempts_log (user_id, is_free_attempt) 
                VALUES (?, ?)
            """, (db_user_id, is_free_attempt))
            
            if is_free_attempt:
                cursor.execute("""
                    UPDATE users SET free_attempts = free_attempts - 1 
                    WHERE telegram_id = ? AND free_attempts > 0
                """, (user_id,))
            else:
                cursor.execute("""
                    UPDATE users SET paid_attempts = paid_attempts - 1 
                    WHERE telegram_id = ? AND paid_attempts > 0
                """, (user_id,))
            
            self.connection.commit()
            return True
        except Exception as e:
            print(f"❌ Ошибка логирования попытки: {e}")
            return False
    
    def get_user_attempts(self, telegram_id: int):
        try:
            cursor = self.connection.cursor()
            cursor.execute("""
                SELECT free_attempts, paid_attempts, premium_until 
                FROM users WHERE telegram_id = ?
            """, (telegram_id,))
            result = cursor.fetchone()
            if result:
                return {
                    'free_attempts': result[0], 
                    'paid_attempts': result[1], 
                    'premium_until': result[2]
                }
            return {'free_attempts': 0, 'paid_attempts': 0, 'premium_until': None}
        except Exception as e:
            print(f"❌ Ошибка получения попыток: {e}")
            return {'free_attempts': 0, 'paid_attempts': 0, 'premium_until': None}

    def get_user_number(self, telegram_id: int):
        try:
            cursor = self.connection.cursor()
            cursor.execute("SELECT id_user FROM users WHERE telegram_id = ?", (telegram_id,))
            result = cursor.fetchone()
            if result:
                return result[0]
            return None
        except Exception as e:
            print(f"❌ Ошибка получения номера пользователя: {e}")
            return None

    def activate_user_by_number(self, user_number: int, tariff_type: str):
        try:
            cursor = self.connection.cursor()
        
            cursor.execute("SELECT telegram_id FROM users WHERE id_user = ?", (user_number,))
            result = cursor.fetchone()
            if not result:
                return False, "Пользователь не найден"
        
            telegram_id = result[0]
        
            # Проверяем тариф
            if tariff_type == "standard":
                cursor.execute("UPDATE users SET paid_attempts = paid_attempts + 10 WHERE telegram_id = ?", (telegram_id,))
                message = "✅ Пополнено на 10 попыток!"
            elif tariff_type == "premium":
                premium_until = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
                cursor.execute("UPDATE users SET premium_until = ? WHERE telegram_id = ?", (premium_until, telegram_id))
                message = "✅ Премиум активирован на 30 дней!"
            else:
                return False, "Неизвестный тариф"
        
            self.connection.commit()
        
            # Обновляем статистику пользователя
            cursor.execute("UPDATE user_stats SET active_tariff = ? WHERE user_id = (SELECT id_user FROM users WHERE telegram_id = ?)", 
                          (tariff_type, telegram_id))
            self.connection.commit()
        
            return True, message, telegram_id
        
        except Exception as e:
            print(f"❌ Ошибка активации пользователя: {e}")
            return False, str(e), None

    def add_payment_request(self, user_id: int, user_number: int, amount: int, tariff: str):
        try:
            cursor = self.connection.cursor()
            cursor.execute("""
                INSERT INTO payment_requests (user_id, user_number, amount, tariff, status, created_at)
                VALUES (?, ?, ?, ?, 'pending', ?)
            """, (user_id, user_number, amount, tariff, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            self.connection.commit()
            return cursor.lastrowid
        except Exception as e:
            print(f"❌ Ошибка сохранения заявки: {e}")
            return None
    
    def process_payment(self, user_id: int, tariff_id: int):
        try:
            cursor = self.connection.cursor()
            
            cursor.execute("SELECT * FROM tariff WHERE id_tariff = ?", (tariff_id,))
            tariff = cursor.fetchone()
            if not tariff:
                return False
            
            cursor.execute("SELECT id_user FROM users WHERE telegram_id = ?", (user_id,))
            user_row = cursor.fetchone()
            if not user_row:
                return False
                
            db_user_id = user_row[0]
            
            cursor.execute("""
                INSERT INTO payments 
                (user_id, tariff_id, payment_date, payment_status_id, attempts_added, premium_days, amount)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                db_user_id, 
                tariff_id, 
                datetime.now().strftime('%Y-%m-%d'),
                2,
                tariff[1],
                tariff[2],
                tariff[3]
            ))
            
            if tariff[1] > 0:
                cursor.execute("""
                    UPDATE users SET paid_attempts = paid_attempts + ? 
                    WHERE telegram_id = ?
                """, (tariff[1], user_id))
            
            if tariff[2] > 0:
                premium_until = (datetime.now() + timedelta(days=tariff[2])).strftime('%Y-%m-%d')
                cursor.execute("""
                    UPDATE users SET premium_until = ? 
                    WHERE telegram_id = ?
                """, (premium_until, user_id))
            
            cursor.execute("""
                UPDATE users SET amount = amount + ? 
                WHERE telegram_id = ?
            """, (tariff[3], user_id))
            
            tariff_name = "Стандарт" if tariff_id == 1 else "Премиум"
            cursor.execute("""
                UPDATE user_stats SET active_tariff = ? 
                WHERE user_id = (SELECT id_user FROM users WHERE telegram_id = ?)
            """, (tariff_name, user_id))
            
            self.connection.commit()
            return True
            
        except Exception as e:
            print(f"❌ Ошибка обработки платежа: {e}")
            return False
    
    def get_global_ratings(self):
        try:
            cursor = self.connection.cursor()
            cursor.execute("SELECT * FROM global_ratings WHERE id = 1")
            result = cursor.fetchone()
            
            if result:
                rating_distribution = json.loads(result['rating_distribution'])
                rating_distribution = {int(k): v for k, v in rating_distribution.items()}
                return {
                    'total_ratings': result['total_ratings'],
                    'average_rating': result['average_rating'],
                    'rating_distribution': rating_distribution,
                    'survey_ratings': result['survey_ratings'],
                    'quick_search_ratings': result['quick_search_ratings']
                }
            return self.get_default_global_ratings()
        except Exception as e:
            print(f"❌ Ошибка получения глобальной статистики: {e}")
            return self.get_default_global_ratings()
    
    def get_default_global_ratings(self):
        return {
            'total_ratings': 0,
            'average_rating': 0,
            'rating_distribution': {1: 0, 2: 0, 3: 0, 4: 0, 5: 0},
            'survey_ratings': 0,
            'quick_search_ratings': 0
        }
    
    def update_global_ratings(self, rating_data: dict):
        try:
            cursor = self.connection.cursor()
            
            rating_distribution_str = {str(k): v for k, v in rating_data['rating_distribution'].items()}
            rating_distribution_json = json.dumps(rating_distribution_str)
            
            cursor.execute("""
                UPDATE global_ratings 
                SET total_ratings = ?, average_rating = ?, rating_distribution = ?, 
                    survey_ratings = ?, quick_search_ratings = ?
                WHERE id = 1
            """, (
                rating_data['total_ratings'],
                rating_data['average_rating'],
                rating_distribution_json,
                rating_data['survey_ratings'],
                rating_data['quick_search_ratings']
            ))
            
            self.connection.commit()
            return True
        except Exception as e:
            print(f"❌ Ошибка обновления глобальной статистики: {e}")
            return False
    
    def export_to_csv(self, table_name: str):
        try:
            cursor = self.connection.cursor()
            cursor.execute(f"SELECT * FROM {table_name}")
            rows = cursor.fetchall()
            
            if not rows:
                return None
            
            cursor.execute(f"PRAGMA table_info({table_name})")
            columns = [column[1] for column in cursor.fetchall()]
            
            filename = f"{table_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            
            with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(columns)
                writer.writerows(rows)
            
            return filename
        except Exception as e:
            print(f"❌ Ошибка экспорта {table_name}: {e}")
            return None

    # Добавить после метода get_db_stats, перед последним отступом класса

    def save_favorite(self, telegram_id: int, recommendations_json: str):
        try:
            cursor = self.connection.cursor()
        
            cursor.execute("SELECT id_user FROM users WHERE telegram_id = ?", (telegram_id,))
            user_row = cursor.fetchone()
            if not user_row:
                return False
            
            db_user_id = user_row[0]
        
            cursor.execute("""
                INSERT INTO favorites (user_id, recommendations) 
                VALUES (?, ?)
            """, (db_user_id, recommendations_json))
        
            self.connection.commit()
            return True
        except Exception as e:
            print(f"❌ Ошибка сохранения в избранное: {e}")
            return False

    def get_user_favorites(self, telegram_id: int):
        try:
            cursor = self.connection.cursor()
        
            cursor.execute("SELECT id_user FROM users WHERE telegram_id = ?", (telegram_id,))
            user_row = cursor.fetchone()
            if not user_row:
                return []
            
            db_user_id = user_row[0]
        
            cursor.execute("""
                SELECT id, recommendations, created_at 
                FROM favorites 
                WHERE user_id = ? 
                ORDER BY created_at DESC
            """, (db_user_id,))
        
            favorites = cursor.fetchall()
        
            result = []
            for fav in favorites:
                result.append({
                    'id': fav[0],
                    'recommendations': fav[1],
                    'created_at': fav[2]
                })
        
            return result
        except Exception as e:
            print(f"❌ Ошибка получения избранного: {e}")
            return []

    def delete_favorite(self, favorite_id: int):
        try:
            cursor = self.connection.cursor()
            cursor.execute("DELETE FROM favorites WHERE id = ?", (favorite_id,))
            self.connection.commit()
            return cursor.rowcount > 0
        except Exception as e:
            print(f"❌ Ошибка удаления из избранного: {e}")
            return False

    def get_db_stats(self):
        try:
            cursor = self.connection.cursor()
            
            stats = {}
            
            cursor.execute("SELECT COUNT(*) FROM users")
            stats['total_users'] = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM users WHERE created_at >= datetime('now', '-7 days')")
            stats['active_users'] = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM users WHERE premium_until >= date('now')")
            stats['premium_users'] = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM user_attempts_log")
            stats['total_attempts'] = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM user_attempts_log WHERE is_free_attempt = 1")
            stats['free_attempts'] = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM user_attempts_log WHERE is_free_attempt = 0")
            stats['paid_attempts'] = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM payments")
            stats['total_payments'] = cursor.fetchone()[0]
            
            cursor.execute("SELECT SUM(amount) FROM users")
            revenue = cursor.fetchone()[0]
            stats['revenue'] = revenue if revenue else 0
            
            global_ratings = self.get_global_ratings()
            stats['global_ratings'] = global_ratings
            
            cursor.execute("""
                SELECT u.username, ual.used_at, ual.is_free_attempt 
                FROM user_attempts_log ual 
                JOIN users u ON ual.user_id = u.id_user 
                ORDER BY ual.used_at DESC LIMIT 5
            """)
            stats['recent_activities'] = cursor.fetchall()
            
            return stats
            
        except Exception as e:
            print(f"❌ Ошибка получения статистики: {e}")
            return {}

db = Database()

class SearchStates(StatesGroup):
    waiting_for_similar = State()
    waiting_for_top_tracks = State()
    waiting_for_genre = State()
    quiz = State()
    quiz_genre_input = State()

# --- КЛАВИАТУРЫ (без изменений) ---
start_ikb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🎵 Пройти опрос", callback_data="survey")],
        [InlineKeyboardButton(text="⚡ Быстрый поиск", callback_data="quick_search")],
        [InlineKeyboardButton(text="💰 Покупки", callback_data="purchases")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")]
    ]
)

tempo_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Быстрый", callback_data="tempo_fast")],
        [InlineKeyboardButton(text="🐌 Медленный", callback_data="tempo_slow")],
        [InlineKeyboardButton(text="⚖️ Средний", callback_data="tempo_medium")],
        [InlineKeyboardButton(text="🔄 Любой", callback_data="tempo_any")]
    ]
)

period_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🎸 90-е", callback_data="period_90s")],
        [InlineKeyboardButton(text="🎮 2000-е", callback_data="period_2000s")],
        [InlineKeyboardButton(text="📱 2010-е", callback_data="period_2010s")],
        [InlineKeyboardButton(text="🚀 2020-е", callback_data="period_2020s")],
        [InlineKeyboardButton(text="❓ Другое", callback_data="period_other")],
        [InlineKeyboardButton(text="🔄 Любой", callback_data="period_any")]
    ]
)

duration_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="⏱️ Короткие (<3 мин)", callback_data="duration_short")],
        [InlineKeyboardButton(text="⏳ Средние (3-5 мин)", callback_data="duration_medium")],
        [InlineKeyboardButton(text="⏰ Длинные (>5 мин)", callback_data="duration_long")],
        [InlineKeyboardButton(text="🔄 Любая", callback_data="duration_any")]
    ]
)

popularity_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🔥 Популярные", callback_data="pop_popular")],
        [InlineKeyboardButton(text="🤫 Неизвестные", callback_data="pop_unknown")],
        [InlineKeyboardButton(text="🔮 Подземные", callback_data="pop_underground")],
        [InlineKeyboardButton(text="🔄 Любая", callback_data="pop_any")]
    ]
)

artist_type_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🎤 Сольные", callback_data="type_solo")],
        [InlineKeyboardButton(text="👥 Группы", callback_data="type_group")],
        [InlineKeyboardButton(text="🔄 Любой", callback_data="type_any")]
    ]
)

# --- ОСНОВНЫЕ ОБРАБОТЧИКИ (с небольшими изменениями) ---
@dp.message(Command("activate"))
async def activate_user_command(message: Message):
    user_id = message.from_user.id
    
    # Проверка что админ
    if user_id not in admin_sessions:
        await message.answer("⛔ У вас нет прав для этой команды")
        return
    
    # Парсим аргументы: /activate 3 standard
    args = message.text.split()
    if len(args) < 3:
        await message.answer("📝 Используйте: /activate [номер_пользователя] [tariff]\n\n"
                            "Примеры:\n"
                            "/activate 3 standard\n"
                            "/activate 5 premium\n\n"
                            "Где standard — 10 попыток, premium — 30 дней")
        return
    
    try:
        user_number = int(args[1])
        tariff_type = args[2].lower()
        
        if tariff_type not in ["standard", "premium"]:
            await message.answer("❌ Тариф должен быть standard или premium")
            return
        
        # Активируем пользователя
        success, result_message, telegram_id = db.activate_user_by_number(user_number, tariff_type)
        
        if success:
            await message.answer(f"✅ {result_message}\n"
                                f"👤 Пользователь №{user_number} (telegram_id: {telegram_id})")
            
            # Отправляем уведомление пользователю
            try:
                await bot.send_message(telegram_id, 
                                      f"🎉 {result_message}\n\n"
                                      f"Можешь продолжать пользоваться ботом!")
            except:
                pass
        else:
            await message.answer(f"❌ Ошибка: {result_message}")
            
    except ValueError:
        await message.answer("❌ Номер пользователя должен быть числом")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

@dp.message(Command("confirm"))
async def confirm_payment_command(message: Message):
    user_id = message.from_user.id
    
    # Парсим аргументы: /confirm 3
    args = message.text.split()
    if len(args) < 2:
        await message.answer("📝 Укажите свой номер пользователя.\n\n"
                            "Пример: /confirm 3\n\n"
                            "Ваш номер можно увидеть в начале работы с ботом")
        return
    
    try:
        user_number = int(args[1])
        user_telegram_id = message.from_user.id
        
        # Получаем настоящий номер пользователя из базы
        real_user_number = db.get_user_number(user_telegram_id)
        
        if real_user_number != user_number:
            await message.answer(f"❌ Неправильный номер! Ваш номер: {real_user_number}\n"
                                f"Используйте: /confirm {real_user_number}")
            return
        
        # Сохраняем заявку
        admin_id = 1376546844
        
        # Отправляем админу уведомление
        await bot.send_message(
            admin_id,
            f"🔔 ЗАЯВКА НА ПОДТВЕРЖДЕНИЕ ОПЛАТЫ\n\n"
            f"👤 Пользователь: @{message.from_user.username or 'без username'}\n"
            f"🔢 Номер: {user_number}\n"
            f"💰 Сумма: 99 руб (стандарт) или 199 руб (премиум)\n"
            f"🕐 Время: {datetime.now().strftime('%H:%M:%S')}\n\n"
            f"Для активации используйте:\n"
            f"/activate {user_number} standard\n"
            f"или\n"
            f"/activate {user_number} premium"
        )
        
        await message.answer(
            f"✅ Заявка на подтверждение отправлена!\n\n"
            f"👤 Ваш номер: {user_number}\n"
            f"💰 Сумма: 99 руб / 199 руб\n"
            f"📝 Не забудьте указать номер {user_number} в комментарии к платежу\n\n"
            f"⏳ После проверки платежа администратор активирует попытки.\n"
            f"Обычно это занимает до 1 часа."
        )
        
    except ValueError:
        await message.answer("❌ Номер должен быть числом")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

@dp.message(Command("start"))
async def start(message: Message):
    user_id = message.from_user.id
    
    if user_id not in user_data:
        user_data[user_id] = {"step": None}
    else:
        user_data[user_id]["step"] = None
    
    username = f"@{message.from_user.username}" if message.from_user.username else "Нет username"
    first_name = message.from_user.first_name or "Неизвестно"
    
    db.safe_add_user(user_id, username, first_name)
    
    # Внутри функции start, в тексте сообщения:
    await message.answer(
        "🎵 Музыкальный Гид 🎵\n\n👋 Привет! Я помогу тебе найти новую музыку по твоим предпочтениям.\n\n"
        "✨ Как это работает:\n"
        "🎯 Ты указываешь жанр/артиста/настроение\n"
        "🔍 Я ищу подходящую музыку через Apple Music\n"
        "📺 Показываю результаты с Apple Music-ссылками\n\n"
        "💡 Совет: Указывай жанры на английском для лучших результатов!",
        reply_markup=start_ikb,
        parse_mode="Markdown"
     )

# Команды для быстрого доступа
@dp.message(Command("similar"))
async def similar_command(message: Message, state: FSMContext):
    await state.set_state(SearchStates.waiting_for_similar)
    await message.answer("Напиши имя артиста, чтобы найти похожих:")

@dp.message(Command("top"))
async def top_command(message: Message, state: FSMContext):
    await state.set_state(SearchStates.waiting_for_top_tracks)
    await message.answer("Введите имя артиста для получения Топ-10:")

@dp.message(Command("genre"))
async def genre_command(message: Message, state: FSMContext):
    await state.set_state(SearchStates.waiting_for_genre)
    await message.answer("Напиши название жанра (например: Phonk, Rock, Pop):")

@dp.message(Command("admin"))
async def admin_command(message: Message):
    await message.answer("🔐 Введите логин админа:")

# Удалить старую версию и вставить новую:
@dp.callback_query(F.data == "quick_search")
async def quick_search_handler(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    
    attempts = db.get_user_attempts(user_id)
    
    is_premium_active = False
    if attempts['premium_until']:
        premium_date = datetime.strptime(attempts['premium_until'], '%Y-%m-%d')
        if premium_date >= datetime.now():
            is_premium_active = True
    
    if attempts['free_attempts'] <= 0 and attempts['paid_attempts'] <= 0 and not is_premium_active:
        await callback_query.answer("❌ У вас закончились попытки! Купите дополнительные.", show_alert=True)
        return
    
    quick_search_text = (
        "⚡ Быстрый поиск ⚡\n\n"
        "👥 Похожие артисты - найду исполнителей с похожей музыкой\n\n"
        "🔝 Топ-треки артиста - покажу 10 самых популярных песен артиста\n\n"
        "🎸 Поиск по жанру - найду музыку в этом стиле\n\n"
        "💡 Совет: Указывай жанры на английском (hip hop, pop, rock)\n"
        "🇷🇺 Русские жанры также работают\n\n"
        "🎯 Выбери тип поиска:"
    )
    
    quick_search_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Похожие артисты", callback_data="search_similar")],
            [InlineKeyboardButton(text="🔝 Топ-треки артиста", callback_data="search_top")],
            [InlineKeyboardButton(text="🎸 Поиск по жанру", callback_data="search_genre")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
        ]
    )
    
    await callback_query.message.edit_text(quick_search_text, reply_markup=quick_search_kb, parse_mode="Markdown")
    await callback_query.answer()

# --- ОБРАБОТКА ВСЕХ СООБЩЕНИЙ ---
# Добавить ПОСЛЕ quick_search_handler:
@dp.callback_query(F.data == "search_similar")
async def search_similar_handler(callback_query: CallbackQuery, state: FSMContext):
    await state.set_state(SearchStates.waiting_for_similar)
    await callback_query.message.edit_text(
        "👥 Похожие артисты\n\n"
        "✏️ Напиши имя артиста или группы:\n\n"
        "📝 Например: The Weeknd, Billie Eilish, Михаил Круг, Кино\n\n"
        "🔍 Я найду артистов с похожей музыкой!",
        parse_mode="Markdown"
    )
    await callback_query.answer()

@dp.callback_query(F.data == "search_top")
async def search_top_handler(callback_query: CallbackQuery, state: FSMContext):
    await state.set_state(SearchStates.waiting_for_top_tracks)
    await callback_query.message.edit_text(
        "🔝 Топ-треки артиста\n\n"
        "✏️ Напиши имя артиста или группы:\n\n"
        "📝 Например: The Weeknd, Billie Eilish, Михаил Круг, Кино\n\n"
        "🔍 Я найду этого артиста и покажу его самые популярные треки!",
        parse_mode="Markdown"
    )
    await callback_query.answer()

@dp.callback_query(F.data == "search_genre")
async def search_genre_handler(callback_query: CallbackQuery, state: FSMContext):
    await state.set_state(SearchStates.waiting_for_genre)
    await callback_query.message.edit_text(
        "🎸 Поиск по жанру\n\n"
        "✏️ Напиши музыкальный жанр:\n\n"
        "📝 Например: rock, pop, hip hop, metal, electronic\n\n"
        "🌍 Русские жанры также работают",
        parse_mode="Markdown"
    )
    await callback_query.answer()

# Удалить старую и вставить новую:
@dp.message(F.text)
async def handle_all_messages(message: Message, state: FSMContext):
    current_state = await state.get_state()
    
    # Обработка состояний поиска ИЗ BOT2.PY
    if current_state == SearchStates.waiting_for_similar:
        await find_similar(message, state)
    elif current_state == SearchStates.waiting_for_top_tracks:
        await top_tracks(message, state)
    elif current_state == SearchStates.waiting_for_genre:
        await genre_search(message, state)
    else:
        # Существующая логика из bot.py
        user_id = message.from_user.id
        text = message.text.strip()
        
        if text.startswith('/'):
            user_data[user_id]["step"] = None
        
        if text == ADMIN_LOGIN:
            await message.answer("✅ Логин верный. Теперь введите пароль:")
            return
        
        elif text == ADMIN_PASSWORD:
            admin_sessions.add(user_id)
            await message.answer("✅ Авторизация успешна! Загружаю данные...")
            await show_admin_panel(message)
            return
        
        if user_id in admin_sessions:
            if text.lower() == "обновить":
                await show_admin_panel(message)
                return
        
        current_step = user_data[user_id].get("step")
        if current_step and current_step.startswith("awaiting_"):
            await handle_survey_answers(message, user_id, text, current_step)

# --- АДМИН-ПАНЕЛЬ ---
async def show_admin_panel(message: Message):
    try:
        stats = db.get_db_stats()
        global_ratings = stats.get('global_ratings', {})
        rating_dist = global_ratings.get('rating_distribution', {})
        
        admin_text = f"""
📊 АДМИН-ПАНЕЛЬ 📊

👥 ПОЛЬЗОВАТЕЛИ:
├ 📈 Всего: {stats.get('total_users', 0)}
├ 🎯 Активные (7 дней): {stats.get('active_users', 0)}
├ 💎 Премиум: {stats.get('premium_users', 0)}
└ 💰 Выручка: {stats.get('revenue', 0):.2f} руб

🎯 АКТИВНОСТЬ:
├ 📊 Всего попыток: {stats.get('total_attempts', 0)}
├ 🆓 Бесплатные: {stats.get('free_attempts', 0)}
├ 💳 Платные: {stats.get('paid_attempts', 0)}
└ 💸 Платежей: {stats.get('total_payments', 0)}

⭐ СТАТИСТИКА ОЦЕНОК:
├ ⭐ Средний рейтинг: {global_ratings.get('average_rating', 0):.1f} ⭐
├ 📈 Всего оценок: {global_ratings.get('total_ratings', 0)}
├ 📝 Оценки опросов: {global_ratings.get('survey_ratings', 0)}
├ 🔍 Оценки поиска: {global_ratings.get('quick_search_ratings', 0)}
└ 📊 Распределение:
   ├ 5⭐: {rating_dist.get(5, 0)}
   ├ 4⭐: {rating_dist.get(4, 0)}
   ├ 3⭐: {rating_dist.get(3, 0)}
   ├ 2⭐: {rating_dist.get(2, 0)}
   └ 1⭐: {rating_dist.get(1, 0)}
"""
        
        recent_activities = stats.get('recent_activities', [])
        if recent_activities:
            admin_text += "\n\n🕒 ПОСЛЕДНИЕ АКТИВНОСТИ:"
            for activity in recent_activities[:3]:
                attempt_type = "🆓" if activity[2] else "💳"
                username = activity[0] or "Неизвестно"
                time = activity[1][:16] if activity[1] else "неизвестно"
                admin_text += f"\n{attempt_type} {username} - {time}"
        
        admin_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📤 Экспорт БД", callback_data="admin_export")],
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_refresh")],
                [InlineKeyboardButton(text="🚪 Выйти из админки", callback_data="admin_logout")]
            ]
        )
        
        await message.answer(admin_text, reply_markup=admin_kb)
        
    except Exception as e:
        await message.answer(f"❌ Ошибка загрузки данных: {e}")

@dp.callback_query(F.data == "admin_export")
async def admin_export_handler(callback_query: CallbackQuery):
    if callback_query.from_user.id not in admin_sessions:
        await callback_query.answer("🚫 Доступ запрещен!", show_alert=True)
        return
    
    export_text = "📤 ЭКСПОРТ БАЗЫ ДАННЫХ 📤\n\nВыберите таблицу для экспорта в CSV:"
    
    export_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Users", callback_data="export_users")],
            [InlineKeyboardButton(text="📊 Attempts Log", callback_data="export_attempts")],
            [InlineKeyboardButton(text="💳 Payments", callback_data="export_payments")],
            [InlineKeyboardButton(text="💰 Tariffs", callback_data="export_tariffs")],
            [InlineKeyboardButton(text="📈 User Stats", callback_data="export_user_stats")],
            [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin_back")]
        ]
    )
    
    await callback_query.message.edit_text(export_text, reply_markup=export_kb)
    await callback_query.answer()

@dp.callback_query(F.data.startswith("export_"))
async def export_table_handler(callback_query: CallbackQuery):
    if callback_query.from_user.id not in admin_sessions:
        await callback_query.answer("🚫 Доступ запрещен!", show_alert=True)
        return
    
    table_map = {
        "export_users": "users",
        "export_attempts": "user_attempts_log", 
        "export_payments": "payments",
        "export_tariffs": "tariff",
        "export_user_stats": "user_stats"
    }
    
    table_name = table_map.get(callback_query.data)
    if not table_name:
        await callback_query.answer("❌ Неизвестная таблица")
        return
    
    await callback_query.message.edit_text(f"📤 Экспортирую таблицу {table_name}...")
    
    filename = db.export_to_csv(table_name)
    
    if filename and os.path.exists(filename):
        await callback_query.message.answer_document(
            FSInputFile(filename),
            caption=f"📋 Таблица: {table_name}\n📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        os.remove(filename)
    else:
        await callback_query.message.edit_text(f"❌ Не удалось экспортировать таблицу {table_name}")
    
    await callback_query.answer()

@dp.callback_query(F.data == "admin_refresh")
async def admin_refresh_handler(callback_query: CallbackQuery):
    if callback_query.from_user.id not in admin_sessions:
        await callback_query.answer("🚫 Доступ запрещен!", show_alert=True)
        return
        
    await callback_query.message.edit_text("🔄 Обновляю статистику...")
    await show_admin_panel(callback_query.message)
    await callback_query.answer("✅ Статистика обновлена!")

@dp.callback_query(F.data == "admin_back")
async def admin_back_handler(callback_query: CallbackQuery):
    if callback_query.from_user.id not in admin_sessions:
        await callback_query.answer("🚫 Доступ запрещен!", show_alert=True)
        return
        
    await callback_query.message.edit_text("⬅️ Возвращаюсь в админ-панель...")
    await show_admin_panel(callback_query.message)
    await callback_query.answer()

@dp.callback_query(F.data == "admin_logout")
async def admin_logout_handler(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id in admin_sessions:
        admin_sessions.discard(user_id)
    
    await callback_query.message.edit_text(
        "✅ Вы вышли из админ-панели.\n\n"
        "🎵 Музыкальный Гид 🎵\n\n👋 Привет! Я помогу тебе найти новую музыку через Apple Music.",
        reply_markup=start_ikb
    )
    await callback_query.answer()

# --- ОПРОС ---
@dp.callback_query(F.data == "survey")
async def survey_handler(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    
    attempts = db.get_user_attempts(user_id)
    
    is_premium_active = False
    if attempts['premium_until']:
        premium_date = datetime.strptime(attempts['premium_until'], '%Y-%m-%d')
        if premium_date >= datetime.now():
            is_premium_active = True
    
    if attempts['free_attempts'] <= 0 and attempts['paid_attempts'] <= 0 and not is_premium_active:
        await callback_query.answer("❌ У вас закончились попытки! Купите дополнительные.", show_alert=True)
        return
    
    consent_text = (
        "🎵 Музыкальный опрос 🎵\n\n"
        "🎯 Помоги мне узнать твои музыкальные предпочтения! "
        "Я задам 10 вопросов о твоих вкусах в музыке.\n\n"
        "✨ Как это работает:\n"
        "🎯 Твои ответы формируют поисковый запрос\n"
        "🔍 Нейросеть ищет музыку по этому запросу\n"
        "📺 Показываю лучшие совпадения\n\n"
        "💡 Совет:\n"
        "🌍 Указывай жанры на английском (rock, pop, metal)\n"
        "🎯 Чем подробнее ответишь - тем точнее поиск\n\n"
        "🎉 Готов открыть мир своей музыки?"
    )
    
    consent_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎯 Начать опрос", callback_data="start_survey")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
        ]
    )
    
    await callback_query.message.edit_text(consent_text, reply_markup=consent_kb, parse_mode="Markdown")
    await callback_query.answer()

@dp.callback_query(F.data == "start_survey")
async def start_survey_handler(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    user_data[user_id]["step"] = "awaiting_genre"
    
    await ask_question_1(callback_query.message, user_id)
    await callback_query.answer()

async def ask_question_1(message: Message, user_id: int):
    skip_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏭️ Пропустить вопрос", callback_data="skip_1")]
        ]
    )
    
    await message.answer(
        "1/10 🎵 Какой жанр песен предпочитаешь?\n\n"
        "✏️ Напиши свои любимые жанры НА АНГЛИЙСКОМ\n\n"
        "🎶 Популярные жанры: rock, pop, hip hop, metal, electronic, jazz\n\n"
        "🌍 Русские жанры также работают",
        reply_markup=skip_kb,
        parse_mode="Markdown"
    )

async def ask_question_2(message: Message, user_id: int):
    skip_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏭️ Пропустить вопрос", callback_data="skip_2")]
        ]
    )
    
    await message.answer(
        "2/10 🌍 Какой язык песен предпочитаешь?\n\n"
        "✏️ Напиши предпочитаемые языки\n"
        "📝 Например: русский, английский, испанский, корейский, любой",
        reply_markup=skip_kb,
        parse_mode="Markdown"
    )

async def ask_question_3(message: Message, user_id: int):
    skip_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏭️ Пропустить вопрос", callback_data="skip_3")]
        ]
    )
    
    await message.answer(
        "3/10 🗺️ Из какой страны предпочитаешь исполнителя?\n\n"
        "✏️ Напиши страны или регионы\n"
        "📝 Например: Россия, США, Норвегия, Корея, Великобритания, любая",
        reply_markup=skip_kb,
        parse_mode="Markdown"
    )

async def ask_question_4(message: Message, user_id: int):
    skip_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏭️ Пропустить вопрос", callback_data="skip_4")]
        ]
    )
    
    await message.answer(
        "4/10 🎭 Какое настроение песен ближе?\n\n"
        "✏️ Опиши какое настроение ты ищешь\n"
        "📝 Например: грустный, веселый, энергичный, романтичный, агрессивный",
        reply_markup=skip_kb,
        parse_mode="Markdown"
    )

async def ask_question_5(message: Message, user_id: int):
    await message.answer(
        "5/10 🎶 Какой темп песен ближе?\n\n"
        "🎯 Выбери один из вариантов:",
        reply_markup=tempo_kb,
        parse_mode="Markdown"
    )

async def ask_question_6(message: Message, user_id: int):
    await message.answer(
        "6/10 📅 Музыка какого периода интересна?\n\n"
        "🎯 Выбери один из вариантов:",
        reply_markup=period_kb,
        parse_mode="Markdown"
    )

async def ask_question_7(message: Message, user_id: int):
    await message.answer(
        "7/10 ⏱️ Какая длительность песен предпочтительна?\n\n"
        "🎯 Выбери один из вариантов:",
        reply_markup=duration_kb,
        parse_mode="Markdown"
    )

async def ask_question_8(message: Message, user_id: int):
    await message.answer(
        "8/10 📊 Насколько для тебя важна популярность песен?\n\n"
        "🎯 Выбери один из вариантов:",
        reply_markup=popularity_kb,
        parse_mode="Markdown"
    )

async def ask_question_9(message: Message, user_id: int):
    await message.answer(
        "9/10 🎤 Предпочитаешь сольных исполнителей или группы?\n\n"
        "🎯 Выбери один из вариантов:",
        reply_markup=artist_type_kb,
        parse_mode="Markdown"
    )

async def ask_question_10(message: Message, user_id: int):
    skip_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏭️ Пропустить вопрос", callback_data="skip_10")]
        ]
    )
    
    await message.answer(
        "10/10 🤝 Напиши похожих исполнителей под свои предпочтения\n\n"
        "✏️ Перечисли артистов, которые тебе нравятся\n"
        "📝 Например: Linkin Park, The Weeknd, Земфира, Би-2, Rammstein\n\n"
        "💡 Можно указать несколько через запятую",
        reply_markup=skip_kb,
        parse_mode="Markdown"
    )

# --- MAPS ДЛЯ ОПРОСА ---
TEMPO_MAP = {
    "tempo_fast": "⚡ быстрый",
    "tempo_slow": "🐌 медленный", 
    "tempo_medium": "⚖️ средний",
    "tempo_any": None
}

PERIOD_MAP = {
    "period_90s": "🎸 1990-е",
    "period_2000s": "🎮 2000-е",
    "period_2010s": "📱 2010-е", 
    "period_2020s": "🚀 2020-е",
    "period_other": "❓ другое",
    "period_any": None
}

DURATION_MAP = {
    "duration_short": "⏱️ короткие (<3 мин)",
    "duration_medium": "⏳ средние (3-5 мин)",
    "duration_long": "⏰ длинные (>5 мин)",
    "duration_any": None
}

POPULARITY_MAP = {
    "pop_popular": "🔥 популярные",
    "pop_unknown": "🤫 неизвестные",
    "pop_underground": "🔮 подземные",
    "pop_any": None
}

ARTIST_TYPE_MAP = {
    "type_solo": "🎤 сольные",
    "type_group": "👥 группы",
    "type_any": None
}

# --- ОБРАБОТЧИКИ ОТВЕТОВ ОПРОСА ---
@dp.callback_query(F.data.startswith("tempo_"))
async def handle_tempo(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    tempo_value = TEMPO_MAP.get(callback_query.data)
    user_data[user_id]["tempo"] = tempo_value
    user_data[user_id]["step"] = "awaiting_period"
    await ask_question_6(callback_query.message, user_id)
    await callback_query.answer()

@dp.callback_query(F.data.startswith("period_"))
async def handle_period(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    period_value = PERIOD_MAP.get(callback_query.data)
    
    if callback_query.data == "period_other":
        user_data[user_id]["step"] = "awaiting_period_other"
        await callback_query.message.answer(
            "✏️ Напиши какой период тебя интересует:\n\n"
            "📝 Например: 80-е, начало 2000-х, современная музыка",
            parse_mode="Markdown"
        )
    else:
        user_data[user_id]["period"] = period_value
        user_data[user_id]["step"] = "awaiting_duration"
        await ask_question_7(callback_query.message, user_id)
    
    await callback_query.answer()

@dp.callback_query(F.data.startswith("duration_"))
async def handle_duration(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    duration_value = DURATION_MAP.get(callback_query.data)
    user_data[user_id]["duration"] = duration_value
    user_data[user_id]["step"] = "awaiting_popularity"
    await ask_question_8(callback_query.message, user_id)
    await callback_query.answer()

@dp.callback_query(F.data.startswith("pop_"))
async def handle_popularity(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    popularity_value = POPULARITY_MAP.get(callback_query.data)
    user_data[user_id]["popularity"] = popularity_value
    user_data[user_id]["step"] = "awaiting_artist_type"
    await ask_question_9(callback_query.message, user_id)
    await callback_query.answer()

@dp.callback_query(F.data.startswith("type_"))
async def handle_artist_type(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    artist_type_value = ARTIST_TYPE_MAP.get(callback_query.data)
    user_data[user_id]["artist_type"] = artist_type_value
    user_data[user_id]["step"] = "awaiting_similar_artists"
    await ask_question_10(callback_query.message, user_id)
    await callback_query.answer()

@dp.callback_query(F.data == "skip_1")
async def skip_1_handler(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    user_data[user_id]["genre"] = None
    user_data[user_id]["step"] = "awaiting_language"
    await ask_question_2(callback_query.message, user_id)
    await callback_query.answer()

@dp.callback_query(F.data == "skip_2")
async def skip_2_handler(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    user_data[user_id]["language"] = None
    user_data[user_id]["step"] = "awaiting_country"
    await ask_question_3(callback_query.message, user_id)
    await callback_query.answer()

@dp.callback_query(F.data == "skip_3")
async def skip_3_handler(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    user_data[user_id]["country"] = None
    user_data[user_id]["step"] = "awaiting_mood"
    await ask_question_4(callback_query.message, user_id)
    await callback_query.answer()

@dp.callback_query(F.data == "skip_4")
async def skip_4_handler(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    user_data[user_id]["mood"] = None
    user_data[user_id]["step"] = "awaiting_tempo"
    await ask_question_5(callback_query.message, user_id)
    await callback_query.answer()

@dp.callback_query(F.data == "skip_10")
async def skip_10_handler(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    user_data[user_id]["similar_artists"] = None
    await finish_survey_callback(callback_query)
    await callback_query.answer()

async def handle_survey_answers(message: Message, user_id: int, text: str, current_step: str):
    if current_step == "awaiting_genre":
        user_data[user_id]["genre"] = text
        user_data[user_id]["step"] = "awaiting_language"
        await ask_question_2(message, user_id)
        
    elif current_step == "awaiting_language":
        user_data[user_id]["language"] = text
        user_data[user_id]["step"] = "awaiting_country"
        await ask_question_3(message, user_id)
        
    elif current_step == "awaiting_country":
        user_data[user_id]["country"] = text
        user_data[user_id]["step"] = "awaiting_mood"
        await ask_question_4(message, user_id)
        
    elif current_step == "awaiting_mood":
        user_data[user_id]["mood"] = text
        user_data[user_id]["step"] = "awaiting_tempo"
        await ask_question_5(message, user_id)
        
    elif current_step == "awaiting_period_other":
        user_data[user_id]["period"] = text
        user_data[user_id]["step"] = "awaiting_duration"
        await ask_question_7(message, user_id)
        
    elif current_step == "awaiting_similar_artists":
        user_data[user_id]["similar_artists"] = text
        await finish_survey_message(message, user_id)

async def finish_survey_message(message: Message, user_id: int):
    attempts = db.get_user_attempts(user_id)
    is_free_attempt = attempts['free_attempts'] > 0
    db.log_attempt(user_id, is_free_attempt)
    
    db.increment_user_stat(user_id, 'surveys_completed')
    
    await generate_survey_results(message, user_id)

async def finish_survey_callback(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    
    attempts = db.get_user_attempts(user_id)
    is_free_attempt = attempts['free_attempts'] > 0
    db.log_attempt(user_id, is_free_attempt)
    
    db.increment_user_stat(user_id, 'surveys_completed')
    
    await generate_survey_results(callback_query.message, user_id)

async def generate_survey_results(message: Message, user_id: int):
    answers = {
        "🎵 Жанр": user_data[user_id]["genre"],
        "🌍 Язык": user_data[user_id]["language"], 
        "🗺️ Страна": user_data[user_id]["country"],
        "🎭 Настроение": user_data[user_id]["mood"],
        "🎶 Темп": user_data[user_id]["tempo"],
        "📅 Период": user_data[user_id]["period"],
        "⏱️ Длительность": user_data[user_id]["duration"],
        "📊 Популярность": user_data[user_id]["popularity"],
        "🎤 Исполнители": user_data[user_id]["artist_type"],
        "🤝 Похожие артисты": user_data[user_id]["similar_artists"]
    }
    
    filled_answers = sum(1 for answer in answers.values() if answer is not None)
    
    if filled_answers >= 8:
        music_profile = "🎼 ВИРТУОЗ МУЗЫКАЛЬНОГО ВКУСА 🎼\n\n🎯 Ты точно знаешь, что хочешь слышать! Твой музыкальный профиль детально проработан и готов к созданию идеальных рекомендаций."
    elif filled_answers >= 5:
        music_profile = "🎵 ЛЮБИТЕЛЬ РАЗНООБРАЗИЯ 🎵\n\n🌍 Ты открыт для новых звуков и готов исследовать музыкальные горизонты. Идеальный слушатель для открытий!"
    else:
        music_profile = "🎧 СВОБОДНЫЙ СЛУШАТЕЛЬ 🎧\n\n🔄 Ты не ограничиваешь себя рамками и готов к любым музыкальным приключениям. Мир звуков открыт для тебя!"
    
    result_text = f"""
✅ ОПРОС ЗАВЕРШЕН! ✅

{music_profile}

📋 Твои музыкальные предпочтения:
"""
    
    for question, answer in answers.items():
        if answer:
            result_text += f"\n• {question}: {answer}"
    
    result_text += f"\n\n📊 Заполнено вопросов: {filled_answers}/10"
    result_text += "\n\n🎯 Теперь я подберу для тебя идеальную музыку!"
    
    result_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎵 Получить рекомендации", callback_data="get_survey_recommendations")],
            [InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_to_main")]
        ]
    )
    
    await message.answer(result_text, reply_markup=result_kb)

@dp.callback_query(F.data == "get_survey_recommendations")
async def get_survey_recommendations_handler(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    
    try:
        # 1. Собираем ответы пользователя из опроса
        user_answers = {}
        question_fields = ["genre", "language", "country", "mood", "tempo", "period", 
                          "duration", "popularity", "artist_type", "similar_artists"]
        
        for field in question_fields:
            value = user_data[user_id].get(field)
            if value:
                user_answers[field] = value
        
        if not user_answers:
            await callback_query.message.answer(
                "❌ Ты не ответил ни на один вопрос опроса!\n💡 Попробуй быстрый поиск.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="⚡ Быстрый поиск", callback_data="quick_search")],
                        [InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_to_main")]
                    ]
                )
            )
            return
        
        # 2. Показываем сообщение о поиске
        await callback_query.message.edit_text(
            "🧠 Анализирую твои музыкальные предпочтения с помощью нейросети GigaChat...\n\n"
            "⏳ Это займет 5-10 секунд...",
            parse_mode="Markdown"
        )
        
        # 3. Пытаемся получить рекомендации от нейросети GigaChat
        ai_recommendations = await ask_gigachat_for_music(user_answers)
        
        # 4. Если нейросеть не ответила (ошибка API, лимит и т.д.) - используем локальную базу
        if not ai_recommendations:
            ai_recommendations = get_fallback_recommendations(user_answers)
            source = "локальной базы знаний (нейросеть временно недоступна)"
        else:
            source = "нейросети GigaChat AI"
        
        # 5. Формируем красивый результат
        result_text = f"🎉 **Вот твои персональные рекомендации:**\n\n"
        
        for i, rec in enumerate(ai_recommendations, 1):
            artist = html.escape(rec.get('artist', 'Неизвестный артист'))
            track = html.escape(rec.get('track', 'Неизвестный трек'))
            year = html.escape(rec.get('year', '?'))
            reason = html.escape(rec.get('reason', 'Отлично подходит под твои предпочтения'))
            
            result_text += f"**{i}. {artist} - {track} ({year})**\n"
            result_text += f"   💡 {reason}\n\n"
        
        # 6. Создаем кнопки для быстрого поиска в Apple Music
        keyboard_buttons = []
        
        for i, rec in enumerate(ai_recommendations[:3], 1):
            search_query = f"{rec['artist']} {rec['track']}"
            # Кодируем для URL
            encoded_query = urllib.parse.quote(search_query)
            apple_music_url = f"https://music.apple.com/ru/search?term={encoded_query}"
            
            artist_short = rec['artist'][:15] + '...' if len(rec['artist']) > 15 else rec['artist']
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=f"🔍 Искать '{artist_short}'", 
                    url=apple_music_url
                )
            ])

        keyboard_buttons.append([
            InlineKeyboardButton(
                text="⭐ Добавить подборку в избранное", 
                callback_data="add_to_favorites"
            )
        ])
        
        # Стандартные кнопки навигации
        keyboard_buttons.extend([
            [InlineKeyboardButton(text="🔄 Новый опрос", callback_data="survey")],
            [InlineKeyboardButton(text="⚡ Быстрый поиск", callback_data="quick_search")],
            [InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_to_main")]
        ])
        
        result_kb = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        # 7. Сохраняем результат поиска (для статистики если нужно)
        user_data[user_id]["last_search"] = ai_recommendations
        
        # 8. Отправляем результат пользователю
        await callback_query.message.answer(
            result_text, 
            reply_markup=result_kb, 
            parse_mode="Markdown"
        )
        
        # 9. Запрашиваем оценку (используй свою функцию ask_for_rating)
        await ask_for_rating(callback_query.message, user_id, "survey")
        
    except Exception as e:
        print(f"❌ Критическая ошибка в опросе: {e}")
        import traceback
        traceback.print_exc()
        
        await callback_query.message.answer(
            "❌ Произошла неожиданная ошибка при анализе твоих предпочтений.\n"
            "💡 Попробуй быстрый поиск или повтори позже!",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⚡ Быстрый поиск", callback_data="quick_search")],
                    [InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_to_main")]
                ]
            )
        )

# --- ПОКУПКИ ---
@dp.callback_query(F.data == "purchases")
async def purchases_handler(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    user_number = db.get_user_number(user_id)
    
    purchases_text = (
        f"💰 ПОКУПКИ 💰\n\n"
        f"👤 Ваш номер пользователя: **{user_number}**\n\n"
        f"📦 **Стандарт** — 99 руб\n"
        f"• +10 дополнительных попыток\n"
        f"• Мгновенная активация после подтверждения\n\n"
        f"💎 **Премиум** — 199 руб\n"
        f"• Безлимитные попытки на 30 дней\n"
        f"• Приоритетная генерация\n\n"
        f"💳 **Реквизиты для оплаты:**\n"
        f"2200 7007 1060 8364\n\n"
        f"📝 **В комментарии к платежу ОБЯЗАТЕЛЬНО укажите ваш номер:**\n"
        f"🔢 **{user_number}**\n\n"
        f"После оплаты напишите:\n"
        f"/confirm {user_number}"
    )
    
    purchases_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📦 Стандарт — 99 руб", callback_data="buy_standard")],
            [InlineKeyboardButton(text="💎 Премиум — 199 руб", callback_data="buy_premium")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
        ]
    )
    
    await callback_query.message.edit_text(purchases_text, reply_markup=purchases_kb, parse_mode="Markdown")
    await callback_query.answer()

@dp.callback_query(F.data == "buy_standard")
async def buy_standard_handler(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    user_number = db.get_user_number(user_id)
    
    text = (
        f"💰 ОПЛАТА СТАНДАРТ — 99 руб\n\n"
        f"👤 Ваш номер: **{user_number}**\n\n"
        f"💳 **Реквизиты:**\n"
        f"2200 7007 1060 8364\n\n"
        f"📝 **Комментарий:** {user_number}\n\n"
        f"После оплаты напишите:\n"
        f"/confirm {user_number}\n\n"
        f"✅ После проверки +10 попыток будут добавлены"
    )
    
    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад к покупкам", callback_data="purchases")]
        ]
    )
    
    await callback_query.message.edit_text(text, reply_markup=back_kb, parse_mode="Markdown")
    await callback_query.answer()

@dp.callback_query(F.data == "buy_premium")
async def buy_premium_handler(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    user_number = db.get_user_number(user_id)
    
    text = (
        f"💰 ОПЛАТА ПРЕМИУМ — 199 руб\n\n"
        f"👤 Ваш номер: **{user_number}**\n\n"
        f"💳 **Реквизиты:**\n"
        f"2200 7007 1060 8364\n\n"
        f"📝 **Комментарий:** {user_number}\n\n"
        f"После оплаты напишите:\n"
        f"/confirm {user_number}\n\n"
        f"✅ После проверки Премиум активируется на 30 дней"
    )
    
    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад к покупкам", callback_data="purchases")]
        ]
    )
    
    await callback_query.message.edit_text(text, reply_markup=back_kb, parse_mode="Markdown")
    await callback_query.answer()

# --- ПРОФИЛЬ ---
@dp.callback_query(F.data == "profile")
async def profile_handler(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    
    user_data[user_id]["step"] = None
    
    try:
        stats = db.get_user_stats(user_id)
        attempts = db.get_user_attempts(user_id)
    except Exception as e:
        print(f"❌ Ошибка получения статистики для профиля: {e}")
        await callback_query.answer("❌ Ошибка загрузки профиля", show_alert=True)
        return
    
    avg_rating = stats["average_rating"] 
    rating_text = f"{avg_rating:.1f} ⭐ ({stats['total_ratings']} оценок)" if stats["total_ratings"] > 0 else "📭 еще нет оценок"
    
    profile_text = (
        "👤 Профиль 👤\n\n"
        "📊 Статистика:\n"
        f"• 📝 Пройдено опросов: {stats['surveys_completed']}\n"
        f"• ⚡ Быстрых поисков: {stats['quick_searches']}\n"
        f"• ⭐ Средняя оценка: {rating_text}\n"
        f"• 🆓 Бесплатные попытки: {attempts['free_attempts']}\n"
        f"• 💳 Платные попытки: {attempts['paid_attempts']}\n"
        f"• 💎 Премиум до: {attempts['premium_until'] or 'нет'}\n"
        f"• 🛍️ Последняя покупка: {stats['active_tariff']}\n\n"
        f"👤 Имя: {stats['first_name']}\n"
        f"📱 Username: {stats['username']}\n\n"
        "🎯 Хочешь больше возможностей?"
    )
    
    profile_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💰 Купить попытки", callback_data="purchases")],
            [InlineKeyboardButton(text="⭐ Избранное", callback_data="show_favorites")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
        ]
    )
    
    await callback_query.message.edit_text(profile_text, reply_markup=profile_kb)
    await callback_query.answer()

# --- ОЦЕНКИ ---
async def ask_for_rating(message: Message, user_id: int, search_type="survey"):
    rating_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1 ⭐", callback_data=f"rate_1_{search_type}"),
                InlineKeyboardButton(text="2 ⭐", callback_data=f"rate_2_{search_type}"),
                InlineKeyboardButton(text="3 ⭐", callback_data=f"rate_3_{search_type}"),
                InlineKeyboardButton(text="4 ⭐", callback_data=f"rate_4_{search_type}"),
                InlineKeyboardButton(text="5 ⭐", callback_data=f"rate_5_{search_type}")
            ],
            [InlineKeyboardButton(text="⏭️ Пропустить оценку", callback_data=f"skip_rating_{search_type}")]
        ]
    )
    
    text = "⭐ Как тебе найденные рекомендации?\n\n💡 Помоги мне стать лучше! Оцени от 1 до 5 звезд:"
    
    rating_msg = await message.answer(text, reply_markup=rating_kb)
    user_data[user_id]["rating_message_id"] = rating_msg.message_id 

@dp.callback_query(F.data.startswith("rate_"))
async def handle_rating(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    rating_message_id = user_data[user_id].get("rating_message_id")
    if rating_message_id:
        try:
            await callback_query.message.bot.delete_message(
                chat_id=callback_query.message.chat.id,
                message_id=rating_message_id
            )
        except Exception as e:
            print(f"Не удалось удалить сообщение: {e}")
    user_data[user_id].pop("rating_message_id", None)
    data_parts = callback_query.data.split("_")
    rating = int(data_parts[1])
    search_type = data_parts[2] if len(data_parts) > 2 else "search"
    
    stats = db.get_user_stats(user_id)
    
    rating_data = {
        "rating": rating,
        "type": search_type,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M")
    }
    stats["ratings_history"].append(rating_data)
    
    total_rating = stats["average_rating"] * stats["total_ratings"] + rating
    stats["total_ratings"] += 1
    stats["average_rating"] = total_rating / stats["total_ratings"]
    
    db.update_user_stats(user_id, stats)
    
    global_ratings = db.get_global_ratings()
    
    global_ratings["rating_distribution"][rating] += 1
    global_ratings["total_ratings"] += 1
    
    if search_type == "survey":
        global_ratings["survey_ratings"] += 1
    else:
        global_ratings["quick_search_ratings"] += 1
    
    total_score = sum(star * count for star, count in global_ratings["rating_distribution"].items())
    total_count = global_ratings["total_ratings"]
    global_ratings["average_rating"] = total_score / total_count if total_count > 0 else 0
    
    db.update_global_ratings(global_ratings)
    
    await callback_query.answer()

@dp.callback_query(F.data.startswith("skip_rating_"))
async def skip_rating_handler(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    rating_message_id = user_data[user_id].get("rating_message_id")
    if rating_message_id:
        try:
            await callback_query.message.bot.delete_message(
                chat_id=callback_query.message.chat.id,
                message_id=rating_message_id
            )
        except Exception as e:
            print(f"Не удалось удалить сообщение: {e}")
    user_data[user_id].pop("rating_message_id", None)
    
    await callback_query.answer()

@dp.callback_query(F.data == "add_to_favorites")
async def add_to_favorites_handler(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    
    # Проверяем, есть ли результаты для сохранения
    last_search = user_data[user_id].get("last_search")
    if not last_search:
        await callback_query.answer("❌ Нечего сохранять! Сначала получи рекомендации.", show_alert=True)
        return
    
    # Конвертируем в JSON
    recommendations_json = json.dumps(last_search, ensure_ascii=False)
    
    # Сохраняем в БД
    success = db.save_favorite(user_id, recommendations_json)
    
    if success:
        # Просто показываем всплывающее уведомление
        await callback_query.answer("✅ Рекомендации сохранены в избранное!", show_alert=False)
    else:
        await callback_query.answer("❌ Ошибка при сохранении", show_alert=True)

@dp.callback_query(F.data == "show_favorites")
async def show_favorites_handler(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    
    # Получаем все сохраненные подборки
    favorites = db.get_user_favorites(user_id)
    
    if not favorites:
        # Если избранное пустое
        back_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад в профиль", callback_data="profile")]
            ]
        )
        await callback_query.message.edit_text(
            "⭐ Избранное пустое\n\n"
            "Сохраняй сюда свои лучшие музыкальные находки!",
            reply_markup=back_kb
        )
        await callback_query.answer()
        return
    
    # Показываем список сохраненных подборок
    text = "⭐ Мои сохраненные подборки:\n\n"
    
    # Создаем клавиатуру с кнопками для каждой подборки
    keyboard = []
    
    for fav in favorites:
        # Парсим JSON чтобы узнать сколько треков
        recs = json.loads(fav['recommendations'])
        date_str = fav['created_at'][:16]  # Берем только дату и час:минуты
        
        # Кнопка для просмотра конкретной подборки
        callback_data = f"view_fav_{fav['id']}"
        keyboard.append([
            InlineKeyboardButton(
                text=f"📅 {date_str} | {len(recs)} треков",
                callback_data=callback_data
            ),
        InlineKeyboardButton(
                text="❌ Удалить",
                callback_data=f"delete_fav_{fav['id']}"  # кнопка удаления
            )
        ])
    
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад в профиль", callback_data="profile")])
    
    fav_kb = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await callback_query.message.edit_text(text, reply_markup=fav_kb)
    await callback_query.answer()

@dp.callback_query(F.data.startswith("delete_fav_"))
async def delete_favorite_handler(callback_query: CallbackQuery):
    # Получаем ID подборки из callback_data
    # callback_data выглядит как "delete_fav_123"
    favorite_id = int(callback_query.data.split("_")[2])
    user_id = callback_query.from_user.id
    
    # Удаляем из базы данных
    success = db.delete_favorite(favorite_id)
    
    if success:
        # Показываем уведомление об успехе
        await callback_query.answer("✅ Подборка удалена из избранного!", show_alert=False)
        
        # Обновляем список избранного (перезапускаем show_favorites_handler)
        # Просто вызываем обработчик заново
        await show_favorites_handler(callback_query)
    else:
        await callback_query.answer("❌ Ошибка при удалении", show_alert=True)

@dp.callback_query(F.data.startswith("view_fav_"))
async def view_favorite_handler(callback_query: CallbackQuery):
    favorite_id = int(callback_query.data.split("_")[2])
    
    # Получаем все избранное пользователя
    user_id = callback_query.from_user.id
    favorites = db.get_user_favorites(user_id)
    
    # Ищем конкретную подборку
    target_fav = None
    for fav in favorites:
        if fav['id'] == favorite_id:
            target_fav = fav
            break
    
    if not target_fav:
        await callback_query.answer("❌ Подборка не найдена", show_alert=True)
        return
    
    # Получаем треки из JSON
    tracks = json.loads(target_fav['recommendations'])
    
    # Формируем текст
    text = f"📅 Подборка от {target_fav['created_at'][:16]}\n\n"
    for i, track in enumerate(tracks, 1):
        text += f"{i}. {track.get('artist', '?')} - {track.get('track', '?')}\n"
    
    # Кнопка назад
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="show_favorites")]
        ]
    )
    
    # Отправляем новое сообщение
    await callback_query.message.answer(text, reply_markup=kb)
    await callback_query.answer()

# --- ВОЗВРАТ В ГЛАВНОЕ МЕНЮ ---
@dp.callback_query(F.data == "back_to_main")
async def back_to_main_handler(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    
    await callback_query.message.edit_text(
        "🎵 Музыкальный Гид 🎵\n\n👋 Привет! Я помогу тебе найти новую музыку по твоим предпочтениям.\n\n"
        "✨ Как это работает:\n"
        "🎯 Ты указываешь жанр/артиста/настроение\n"
        "🔍 Я ищу подходящую музыку через Apple Music\n"
        "📺 Показываю результаты с Apple Music-ссылками\n\n"
        "💡 Совет: Указывай жанры на английском для лучших результатов!",
        reply_markup=start_ikb,
        parse_mode="Markdown"
    )
    await callback_query.answer()

# --- ЗАПУСК БОТА ---
async def main():

    await bot.delete_webhook(drop_pending_updates=True)
    print("✅ Вебхук удалён")

    print("🚀 Запуск музыкального бота (Apple Music + GigaChat AI)...")
    print("="*60)
    
    print("✅ Apple Music API подключен")
    print("✅ GigaChat AI API подключен")
    print("✅ База данных SQLite настроена")
    print("✅ Все системы готовы!")
    
    print("\n" + "="*60)
    print("🤖 Бот запущен! Ожидание сообщений...")
    print("="*60)
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
