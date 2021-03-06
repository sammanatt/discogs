import os
import sys
import json
import time
import pprint
import certifi
import requests
import argparse
from tqdm import tqdm
import elasticsearch.helpers
from requests.auth import HTTPBasicAuth
from elasticsearch import Elasticsearch

pp = pprint.PrettyPrinter(indent=4)

# Load environment file, assign variables
from dotenv import load_dotenv
load_dotenv()

elasticsearch_user = os.environ["elasticsearch_user"]
elasticsearch_password = os.environ["elasticsearch_password"]
elasticsearch_connection_string = os.environ["elasticsearch_connection_string"]
elasticsearch_port = os.environ["elasticsearch_port"]
discogs_username = os.environ["discogs_username"]
discogs_token = os.environ["discogs_token"]

# Connecting to ES
try:
    es = Elasticsearch([str(elasticsearch_connection_string)],
        http_auth=(elasticsearch_user,elasticsearch_password),
        port=elasticsearch_port,
        use_ssl=True,
        verify_certs=True,
        ca_certs=certifi.where(),
    )
    #print("Connected {}".format(es.info()))
except Exception as ex:
    print("Error: {}".format(ex))


def discogs_user_verification():
    """
    Validates that the supplied username exists within Discogs.
    """
    url = "https://api.discogs.com/"
    user_collection = requests.get(url+"users/"+str(args.user)+"/collection/folders/0")
    #if statement to confirm that a valid token has been provided.
    if bool(discogs_token) is True:
        try:
            token_check = requests.get(url+"users/"+str(args.user), headers={'Authorization':'Discogs token='+discogs_token}).json()
            if bool(token_check['email']) is True:
                pass
        except KeyError:
                exit(f"The Discogs token provided for {args.user} is invalid. Please update .env with a valid token."  )
    # if statement to verify a valid Discogs user has been supplied.
    if user_collection.status_code == 200:
        collection_string = user_collection.json()
        collection_count = collection_string['count']
        return collection_count
    else:
        exit(f"\nERROR {user_collection.status_code}: {user_collection.json()['message']} \nPlease check for typos in Discogs username or sign up for an account at: https://accounts.discogs.com/register?login_challenge=5cc9a3696af745a2a1f7ac4d523de053")


def get_all_ids():
    """
    Create a list of all existing _id values within the discogs_USERNAME index.
    If index does not exist, one will be created.
    """
    es_id_list = []
    try:
        get_ids = elasticsearch.helpers.scan(es,
                                        query={"query": {"match_all": {}}},
                                        index="discogs_"+args.user,
                                        )
        for i in get_ids:
            es_id_list.append(i['_id'])
        return es_id_list
    except elasticsearch.exceptions.NotFoundError:
        es.indices.create(index='discogs_'+args.user)
        return es_id_list


def discogs_es_sync(discogs_username):
    print("""
******************************
Fetching Elasticsearch _ids...
******************************""")
    existing_ids = get_all_ids()
    print("""
**********************************
Scanning Discogs for new albums...
**********************************""")
    collection_count = discogs_user_verification()
    page = 1
    url = "https://api.discogs.com/"
    auth_sleep = 3 #3 seconds sleep allows for unathenticated requests to avoid being rate limited.
    albums = requests.get(url+"users/"+str(discogs_username)+"/collection/folders/0/releases?page="+str(page)+"&per_page=100").json()
    if bool(discogs_token) is True:
        albums = requests.get(url+"users/"+str(discogs_username)+"/collection/folders/0/releases?page="+str(page)+"&per_page=100",headers={'Authorization':'Discogs token='+discogs_token}).json()
        auth_sleep = 1 #1 second sleep with an authenticated request will take advatage of increased rate limit (60 request per minute)
    total_pages = albums["pagination"]["pages"]
    discogs_library = []
    #with tqdm(total = collection_count) as progress_bar:
    progress_bar = tqdm(total = collection_count)
    while page <= total_pages:
        try:
            for i in albums["releases"]:
                progress_bar.update(1)
                discogs_library.append(i['date_added'])
                #date added was selected as the es_id as it's the unique timestamp a user added the entry to their collection.
                es_id = i['date_added']
                if es_id in existing_ids:
                    progress_bar.set_description(f"Album exists: {i['basic_information']['title']} by {i['basic_information']['artists'][0]['name']}")
                elif es_id not in existing_ids:
                    progress_bar.set_description(f"New album!!!  {i['basic_information']['title']} by {i['basic_information']['artists'][0]['name']}")
                    es.index(index='discogs_'+discogs_username, doc_type='_doc', id=es_id, body=i)
                time.sleep(auth_sleep)
            page = page + 1
        except requests.exceptions.ConnectionError:
            print("API refused connection.")
    progress_bar.close()
    print("""
******************
Running cleanup...
******************""")
    # Delete Elasticsearch documents that no longer exist in Discogs library
    counter = 1
    for i in existing_ids:
        if i not in discogs_library:
            id_to_delete = es.get(index="discogs_"+args.user, id=i)
            print(f"Deleting _id: {i} ({id_to_delete['_source']['basic_information']['title']} by {id_to_delete['_source']['basic_information']['artists'][0]['name']})")
            es.delete(index='discogs_'+discogs_username, doc_type='_doc', id=i)
            counter +=1
    if counter == 1:
        print("No records to cleanup. Elasticsearch is up to date.")

def main(args):
    discogs_es_sync(args.user)


if __name__ == "__main__":
        # Build argument parser
        parser = argparse.ArgumentParser(description='Syncronize a Discogs collection with an Elasticsearch index.')
        parser.add_argument('-u',
                            '--user',
                          default=None,
                          help="Discogs user to import from.",
                          type=str)
        args = parser.parse_args()

        if args.user is None:
            args.user = discogs_username
        main(args)
