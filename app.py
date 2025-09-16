import os
import random
import time
import threading
from flask import Flask, jsonify, render_template
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# --- YouTube API 인증 정보 ---
# 1단계에서 발급받은 자신의 YouTube API 키를 입력하세요.
with open('.key', 'r') as f:
    YOUTUBE_API_KEY = f.read().strip()
    
# YouTube API 클라이언트 빌드
try:
    youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
except Exception as e:
    print(f"YouTube API 클라이언트 초기화 실패: {e}")
    youtube = None

app = Flask(__name__)

# --- 캐시 및 스레드 설정 ---
COMMENT_CACHE = []
CACHE_LOCK = threading.Lock()
TARGET_CACHE_SIZE = 100

def fetch_comments_batch():
    """
    YouTube API로부터 댓글 '묶음'을 가져와 리스트로 반환하는 함수.
    """
    if not youtube:
        print("API 클라이언트가 없어 댓글을 가져올 수 없습니다.")
        return []

    try:
        # 1. 한국의 인기 동영상 목록 50개를 가져옴
        videos_request = youtube.videos().list(
            part="snippet,id",
            chart="mostPopular",
            regionCode="KR", # 한국 인기 동영상
            maxResults=50
        )
        videos_response = videos_request.execute()
        
        if not videos_response.get("items"):
            print("인기 동영상 목록을 가져오는 데 실패했습니다.")
            return []
            
        # 2. 가져온 동영상 중 하나를 무작위로 선택
        random_video = random.choice(videos_response["items"])
        video_id = random_video["id"]
        print(f"선택된 동영상: '{random_video['snippet']['title']}' (ID: {video_id})")

        # 3. 해당 동영상의 댓글 스레드 목록을 가져옴
        #    (댓글이 비활성화된 영상도 있으므로 try-except로 감싸기)
        try:
            comments_request = youtube.commentThreads().list(
                part="snippet",
                videoId=video_id,
                maxResults=100, # 한 번에 최대 100개의 댓글을 가져옴
                order="relevance" # 관련성 높은 순
            )
            comments_response = comments_request.execute()
        except HttpError as e:
            # 댓글이 사용 중지된 경우 등의 에러 처리
            print(f"'{random_video['snippet']['title']}' 영상의 댓글을 가져올 수 없습니다. 이유: {e}")
            return []

        # 4. 가져온 댓글 데이터를 원하는 형식으로 정리
        new_comments = []
        for item in comments_response.get("items", []):
            comment = item["snippet"]["topLevelComment"]["snippet"]
            text = comment["textDisplay"]
            author = comment["authorDisplayName"]
            # 너무 짧거나 긴 댓글, URL이 포함된 댓글 등은 제외
            if 10 < len(text) < 150 and "http" not in text:
                new_comments.append({
                    "text": text,
                    "author": author
                })
        
        print(f"총 {len(new_comments)}개의 유효한 댓글을 찾았습니다.")
        return new_comments

    except Exception as e:
        print(f"YouTube API 호출 중 오류 발생: {e}")
        return []

def producer_task():
    """
    백그라운드에서 주기적으로 실행되며 캐시를 채우는 '생산자' 역할의 함수.
    YouTube API는 X보다 사용량 제한이 넉넉하지만, 동일한 구조를 유지합니다.
    """

    print("백그라운드 생산자 스레드 시작.")
    global COMMENT_CACHE 
    
    while True:
        with CACHE_LOCK:
            if len(COMMENT_CACHE) < TARGET_CACHE_SIZE:
                print(f"현재 캐시: {len(COMMENT_CACHE)}개. 새 댓글을 가져옵니다.")
                fetched_comments = fetch_comments_batch()
                if fetched_comments:
                    COMMENT_CACHE.extend(fetched_comments)
                    # 중복 제거
                    COMMENT_CACHE = [dict(t) for t in {tuple(d.items()) for d in COMMENT_CACHE}]
                    random.shuffle(COMMENT_CACHE)
                    print(f"성공: {len(fetched_comments)}개 추가. 현재 캐시: {len(COMMENT_CACHE)}개")
        
        # 2분(120초)에 한 번씩 새 댓글을 가져옴
        time.sleep(120)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/random-comment')
def api_random_comment():
    with CACHE_LOCK:
        if not COMMENT_CACHE:
            return jsonify({"error": "캐시가 비어있습니다. 잠시 후 다시 시도해주세요."}), 503
        comment_data = random.choice(COMMENT_CACHE)
        return jsonify(comment_data)

if __name__ == '__main__':
    print("서버 시작... 초기 캐시를 채웁니다.")
    while len(COMMENT_CACHE) < TARGET_CACHE_SIZE / 2:
        initial_comments = fetch_comments_batch()
        if initial_comments:
            with CACHE_LOCK:
                COMMENT_CACHE.extend(initial_comments)
                print(f"초기 캐시 채우는 중... 현재 {len(COMMENT_CACHE)}개")
        
        # API 사용량 제한을 위해 3초 대기
        time.sleep(3)

    print(f"초기 캐시 로딩 완료. 현재 캐시: {len(COMMENT_CACHE)}개")

    producer_thread = threading.Thread(target=producer_task, daemon=True)
    producer_thread.start()

    app.run(debug=True)