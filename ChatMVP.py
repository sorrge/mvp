import requests
import time
import datetime
from collections import namedtuple
import re
import os
import random

from bs4 import BeautifulSoup


passcode = None
auth_token_file = "auth_token"
mvp_board = None
mvp_thread_id = None
auth_token = None


def obtain_new_auth_token():
    # Endpoint for passcode authentication
    auth_url = "https://2ch.hk/user/passlogin"

    # Your passcode
    passcode_payload = {'passcode': passcode}

    # Make the authentication request
    auth_response = requests.post(auth_url, data=passcode_payload, params={'json': 1})

    # Check if authentication was successful
    if auth_response.status_code == 200:
        # Access the cookies set by the server
        if 'passcode_auth' in auth_response.cookies:
            return auth_response.cookies['passcode_auth']
        
        raise RuntimeError("Authentication failed or temporary cookie not found.")
    
    raise RuntimeError(f"Authentication failed. Status code: {auth_response.status_code}")


def get_auth_token():
    global auth_token
    if auth_token:
        return auth_token
    
    if not os.path.exists(auth_token_file):
        auth_token = obtain_new_auth_token()
        with open(auth_token_file, 'w') as file:
            file.write(auth_token)
        
        return auth_token

    with open(auth_token_file, 'r') as file:
        auth_token = file.read().strip()
        return auth_token
    

def random_file_name():
    return f"{random.randint(1644080583000, 1707152583000)}.jpg"
    

def create_post(comment, file=None):
    url = "https://2ch.hk/user/posting"

    # Prepare the data payload
    payload = {
        'captcha_type': 'passcode',
        'board': mvp_board,
        # Uncomment and set the 'thread' if you are posting to an existing thread
        'thread': mvp_thread_id,
        'comment': comment,
    }

    if file is None:
        # Prepare the files payload - must have at least one file
        files = {
            # 'file[]': ('filename', open('file_path', 'rb')),
            'dummy': ('', 'dummy content')
        }
    else:
        files = {
            'file[]': (random_file_name(), file),
        }

    cookies = {'passcode_auth': get_auth_token()}

    # Make the request
    response = requests.post(url, files=files, data=payload, cookies=cookies)

    # Check the response
    if response.status_code == 200:
        response_json = response.json()
        if response_json["result"] == 0:
            message = response_json['error']['message']
            raise RuntimeError(f"Posting failed: {message}")
    else:
        response_json = response.json()
        raise RuntimeError(f"Failed to create post. Status code: {response.status_code}. Message: {response_json['error']['message']}")      


def unix_timestamp_to_local_time_string(unix_timestamp):
    # Convert the Unix timestamp to a UTC datetime object
    utc_datetime = datetime.datetime.utcfromtimestamp(unix_timestamp)
    
    # Get the local timezone's offset
    local_offset = datetime.timedelta(seconds=-time.timezone)
    if time.localtime().tm_isdst:
        local_offset += datetime.timedelta(seconds=time.altzone)
    
    # Convert UTC datetime to local datetime
    local_datetime = utc_datetime + local_offset
    
    # Format the local datetime object as a string
    local_time_string = local_datetime.strftime('%a %H:%M:%S')
    
    return local_time_string


# post named tuple
Post = namedtuple('Post', ['num', 'comment', 'date', 'pic_url'])


def parse_posts(posts_json):
    posts = {}
    for post in posts_json:
        comment_html = post["comment"]
        # extract text from HTML piece
        soup = BeautifulSoup(comment_html, 'html.parser')
        # Replace <br> tags with newlines
        for br in soup.find_all("br"):
            br.replace_with("\n")

        text = soup.get_text(separator='', strip=False)
        pic_url = None
        if post["files"]:
            file = post["files"][0]
            if file["type"] in [1, 2]:
                pic_url = f'https://2ch.hk{file["path"]}'

        posts[post["num"]] = Post(post["num"], text, unix_timestamp_to_local_time_string(post["timestamp"]), pic_url)

    return posts    


def get_all_posts():
    # make JSON HTTP request
    response = requests.get(f'https://2ch.hk/{mvp_board}/res/{mvp_thread_id}.json')
    # get JSON from response
    json = response.json()
    return parse_posts(json["threads"][0]["posts"])


def get_updates(last_num):
    try:
        response = requests.get(f'https://2ch.hk/api/mobile/v2/after/{mvp_board}/{mvp_thread_id}/{last_num + 1}')
        json = response.json()
        return parse_posts(json["posts"])
    except Exception:
        return {}


def is_link(text):
    return text.startswith('>>') and text[2:].isdigit()


bad_words = re.compile(r'\b(а|о|на|по|ни)?ху[ийеёяю]|\bбля(\b|д|т)|\b(вы|до|разъ|съ)?[её]б([еиалу]|\b)|пизд|\bпид[оа]р', re.IGNORECASE)
