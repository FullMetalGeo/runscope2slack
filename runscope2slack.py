import requests
import json
import boto3
import time
from slack import WebClient
from PIL import Image, ImageDraw
import math
import os
import logging
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

region = os.getenv('REGION', 'us-east-1')
ssm_path = os.getenv('SSM_PATH', "/applications/runscope2slack").rstrip("/")

def get_parameter(parameter):
    value = os.environ.get(parameter)
    if value is None:
        try:
            response = boto3.client('ssm', region_name=region).get_parameter(
                Name=f"{ssm_path}/{parameter}",
                WithDecryption=True
            )
            value = response["Parameter"]["Value"].rstrip('/')
        except ClientError as e:
            if e.response['Error']['Code'] == "ParameterNotFound":
                logger.error(f"Parameter {parameter} must be set in parameter store, or through environment variable")
            else:
                logger.error(e)
                exit(1)
    return value


def run():
        project = os.environ.get('PROJECT', 'GBDX')
        runscope_apikey = get_parameter('RUNSCOPE_APIKEY')
        headers = {'Authorization': f"Bearer {runscope_apikey}"}
        runscope_bucket = get_parameter('RUNSCOPE_BUCKET')
        sc = WebClient(token=get_parameter('SLACK_TOKEN'))
        slack_channel = get_parameter('SLACK_CHANNEL')

        skiptitles = ['Core WF default domain','Core WF t2medium domain']

        # get list of all tests:
        url = f"https://api.runscope.com/buckets/{runscope_bucket}/tests?count=50"
        r = requests.get(url,headers=headers)
        r.raise_for_status()

        logger.debug(r.json())
        tests = [{'name': test['name'], 'id':test['id']} for test in r.json()['data']]

        # Get all metrics from runscope & average into daily & monthly uptimes
        time_periods = ['day','week','month']
        data = []
        for test in tests:
                if test['name'] in skiptitles:
                        continue
                uptimes = []
                logger.debug(f"getting uptimes for {test['name']}  {test['id']}")
                for period in time_periods:
                        url = f"https://api.runscope.com/buckets/{runscope_bucket}/tests/{test['id']}/metrics?timeframe={period}"
                        r = requests.get(url,headers=headers)
                        all_uptimes = [d['success_ratio'] for d in r.json()['response_times'] if d['success_ratio']]
                        if len(all_uptimes) > 0:
                                uptime = sum(all_uptimes) / len(all_uptimes)
                        else:
                                uptime = 0
                        uptimes.append(uptime)
                data.append({'label':test['name'],'day': round(uptimes[0]*100,3), 'week': round(uptimes[1]*100,3), 'month': round(uptimes[2]*100,3)})



        # python imaging settings

        boxheight = 50
        boxwidth = 280
        num_columns = 3
        num_boxes = len(data)
        logger.debug(f"num_boxes: {num_boxes}")
        num_rows = int(math.ceil(float(num_boxes) / float(num_columns)))
        logger.debug(f"num_rows: {num_rows}")

        image_height = num_rows * boxheight
        image_width = num_columns * boxwidth

        # python imaging constants
        GREEN = (10,200,10)
        RED = (200,55,55)
        BLACK = (0,0,0)
        GREY = (128,128,128)
        red_threshold = 99.0  # turn stuff red if less than this many nines

        ### Turn stats into an image:
        for period in time_periods:
                img = Image.new('RGB', (image_width, image_height), color = (255, 255, 255))
                d = ImageDraw.Draw(img)
                r = 0
                c = 0
                for dat in sorted(data, key = lambda i: i['label']):
                        logger.debug(dat['label'])
                        logger.debug(dat[period])
                        if dat[period] < red_threshold:
                                color = RED
                        elif dat[period] == 0:
                                color = GREY
                        else:
                                color = GREEN
                        d.rectangle([c*boxwidth,r*boxheight,(1+c)*boxwidth,(1+r)*boxheight],fill=color, outline=BLACK)
                        d.text((c*boxwidth+10,r*boxheight+10), dat['label'], fill=BLACK)
                        d.text((c*boxwidth+50,r*boxheight+25), str(dat[period]), fill=BLACK)
                        c = c + 1
                        if c >= num_columns:
                                c = 0
                                r = r + 1
                        if r >= num_rows: r = 0

                img.save(f"/tmp/{period}.png")

                # Post our image to Slack
                response = sc.files_upload(
                    channels=f"#{slack_channel}",
                    file=f"/tmp/{period}.png",
                    title=f"{project} trailing {period} uptime"
                )
                assert response["ok"]

if __name__=='__main__':
        run()

