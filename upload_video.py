import http.client
import httplib2
import os
import random
import sys
import time

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from oauth2client.client import flow_from_clientsecrets
from oauth2client.file import Storage
from oauth2client.tools import argparser, run_flow

# Explicitly tell the underlying HTTP transport library not to retry, since
# we are handling retry logic ourselves.
httplib2.RETRIES = 1
MAX_RETRIES = 5

# Always retry when these exceptions are raised.
RETRIABLE_EXCEPTIONS = (
    httplib2.HttpLib2Error, IOError, http.client.NotConnected,
    http.client.IncompleteRead, http.client.ImproperConnectionState,
    http.client.CannotSendRequest, http.client.CannotSendHeader,
    http.client.ResponseNotReady, http.client.BadStatusLine
)

# Always retry when an apiclient.errors.HttpError with one of these status codes is raised.
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]

# File that contains the OAuth 2.0 information for this application,
# including its client_id and client_secret.
CLIENT_SECRETS_FILE = 'client_secrets.json'

MISSING_CLIENT_SECRETS_MESSAGE = 'WARNING: Please configure OAuth 2.0 (client_secrets.json)'

YOUTUBE_UPLOAD_SCOPE = 'https://www.googleapis.com/auth/youtube.upload'
YOUTUBE_API_SERVICE_NAME = 'youtube'
YOUTUBE_API_VERSION = 'v3'

VALID_PRIVACY_STATUSES = ('public', 'private', 'unlisted')


def get_authenticated_service(args):
    flow = flow_from_clientsecrets(
        CLIENT_SECRETS_FILE,
        scope=YOUTUBE_UPLOAD_SCOPE,
        message=MISSING_CLIENT_SECRETS_MESSAGE
    )

    storage = Storage(f'{sys.argv[0]}-oauth2.json')
    credentials = storage.get()

    if credentials is None or credentials.invalid:
        credentials = run_flow(flow, storage, args)

    return build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION, http=credentials.authorize(httplib2.Http()))


def initialize_upload(youtube, options):
    tags = None
    if options.keywords:
        tags = options.keywords.split(',')

    body = dict(
        snippet=dict(
            title=options.title,
            description=options.description,
            tags=tags,
            categoryId=options.category
        ),
        status=dict(
            privacyStatus=options.privacyStatus
        )
    )

    insert_request = youtube.videos().insert(
        part=','.join(body.keys()),
        body=body,
        media_body=MediaFileUpload(options.file, chunksize=-1, resumable=True)
        # Setting "chunksize" equal to -1 in the code below means that the entire
        # file will be uploaded in a single HTTP request. (If the upload fails,
        # it will still be retried where it left off.)
    )

    resumable_upload(insert_request)


def resumable_upload(insert_request):
    response = None
    error = None
    retry = 0
    while response is None:
        try:
            print('Uploading file...')
            status, response = insert_request.next_chunk()
            if response is not None:
                if 'id' in response:
                    print(f'Video id {response["id"]} was successfully uploaded.')
                else:
                    exit(f'The upload failed an unexpected response: {response}')
        except HttpError as e:
            if e.resp.status in RETRIABLE_STATUS_CODES:
                error = f'A retriable HTTP error {e.resp.status} occurred:\n{e.content}'
            else:
                raise
        except RETRIABLE_EXCEPTIONS as e:
            error = f'A retriable error occurred: {e}'

        if error is not None:
            print(error)
            retry += 1
            if retry > MAX_RETRIES:
                exit('No longer attempting to retry.')

            max_sleep = 2 ** retry
            sleep_seconds = random.random() * max_sleep
            print(f'Sleeping {sleep_seconds} seconds and then retrying...')
            time.sleep(sleep_seconds)


if __name__ == '__main__':
    argparser.add_argument('--file', required=True, help='Video file to upload')
    argparser.add_argument('--title', help='Video title', default='Default title')
    argparser.add_argument('--description', help='Video description', default='Default description')
    argparser.add_argument(
        '--category', default='24',
        help='Numeric video category. ' + 'See https://developers.google.com/youtube/v3/docs/videoCategories/list')
    argparser.add_argument('--keywords', help='Video keywords, comma separated', default='')
    argparser.add_argument('--privacyStatus', choices=VALID_PRIVACY_STATUSES,
                           default=VALID_PRIVACY_STATUSES[0], help='Video privacy status.')
    args = argparser.parse_args()

    if not os.path.exists(args.file):
        exit('Please specify a valid file using the --file= parameter.')

    youtube = get_authenticated_service(args)
    try:
        initialize_upload(youtube, args)
    except HttpError as e:
        print(f'An HTTP error {e.resp.status} occurred:\n{e.content}')
