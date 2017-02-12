from celery.task import task
from celery import states
from celery.exceptions import Ignore
from celery.task.sets import TaskSet
#from dockertask import docker_task
from subprocess import call,STDOUT
#import requests
import os, hashlib, bagit,time
from pymongo import MongoClient
import boto3,shutil,requests
from pandas import read_csv
#Default base directory 
#basedir="/data/static/"


def get_celery_worker_config(api_host):
    celery_worker_hostname = os.getenv('celery_worker_hostname', "dev-mstacy")
    query="?query={'filter':{'celery_worker':'%s'}}" % (celery_worker_hostname)
    url_tmp= "https://{0}/api/catalog/data/catalog/celery_worker_config/.json{1}"     
    req = requests.get(url_tmp.format(api_host,query))
    data = req.json()
    if data['count']>0:
        return data['results'][0]
    raise Exception("Celery Worker Config not in catalog")

@task()
def bags_migrate_s3(s3_bucket='ul-bagit',s3_folder='source-bags',api_host='dev.libraries.ou.edu'):
    """
        This task is used at the OU libraries for the migration of bags from Norfile(OU S2) to AWS S3.
        kwargs:
            s3_bucket='ul-bagit'
            s3_folder='source-bags'
            api_host='dev.libraries.ou.edu'

    """
    #Celery worker Config from Catalog
    celery_config=get_celery_worker_config(api_host)
    #Norfile bag location
    norfile_bagit=celery_config['norfile']['bagit']
    #All Bag Folders with in Norfile
    bags =[name for name in os.listdir(norfile_bagit) if os.path.isdir(os.path.join(norfile_bagit, name,'data'))]
    #s3_bucket=celery_config['s3']['bucket']
    subtasks=[]
    bag_names=[]
    s3 = boto3.client('s3')
    for bag in bags: 
        #double check to make sure not already in s3
        s3_location = "{0}/{1}".format(s3_folder,bag)
        s3_location = s3_location.replace("//","/")
        s3_key = s3.list_objects(Bucket=s3_bucket, Prefix=s3_location ,MaxKeys=1)
        if not 'Contents' in s3_key:
            subtasks.append(upload_bag_s3.subtask(args=(bag,norfile_bagit,s3_bucket,s3_location)))
            bag_names.append(s3_location)

    if subtasks:
        job = TaskSet(tasks=subtasks)
        result_set = job.apply_async()

    names=",".join(bag_names)
    return "{0} subtasks('upload_bag_s3') submitted. Bags: {1}".format(len(subtasks),names)

@task()
def bags_migrate_norfile(olderThanDays=10,api_host='dev.libraries.ou.edu'):
    """
        This task is used at the OU libraries for the migration of bags from Digilab NAS to Norfile(OU S2).
        kwargs:
            olderThanDays=10

    """

    #Celery worker Config from Catalog
    celery_config=get_celery_worker_config(api_host)
    #Bag locations
    norfile_bagit=celery_config['norfile']['bagit']
    nas_bagit= celery_config['nas']['bagit2']
    
    bags=[name for name in os.listdir(nas_bagit) if os.path.isdir(os.path.join(nas_bagit, name,'data'))]
    #Time Variables
    olderThanDays *= 86400 # convert days to seconds
    present = time.time()
    subtasks=[]
    bag_names=[]
    for bag in bags:
        if  (present - os.path.getmtime(os.path.join(nas_bagit, bag))) > olderThanDays:
            if not os.path.isdir("{0}/{1}".format(norfile_bagit,bag)):
                subtasks.append(copy_bag.subtask(args=(bag,"{0}/{1}".format(nas_bagit,bag),"{0}/{1}".format(norfile_bagit,bag))))
                bag_names.append(bag)
    if subtasks:
        job = TaskSet(tasks=subtasks)
        result_set = job.apply_async()

    names=",".join(bag_names)
    return "{0} subtasks('copy_bag') submitted. Bags: {1}".format(len(subtasks),names)

@task(bind=True)
def copy_bag(self,bag_name,source_path,dest_path):
    dest="{0}/{1}".format(dest_path,bag_name)
    source = "{0}/{1}".format(source_path,bag_name)
    if os.path.isdir(dest):
        msg = "Bag destination already exists. Host:{0} Destination: {1}".format(mounts_hostname,dest)
        self.update_state(state=states.FAILURE,meta=msg)
        raise Ignore()
        #return "Bag destination already exists. Host:{0} Destination: {1}".format(mounts_hostname,dest)
    if not os.path.isdir(source):
        msg="Bag source directory does not exist. {0}".format(source)
        self.update_state(state=states.FAILURE,meta=msg)
        raise Ignore()
    shutil.copytree(source, dest)
    return "Bag copied from {0} to {1}".format(source,dest)

@task(bind=True)
def upload_bag_s3(self,bag_name,source_path,s3_bucket,s3_location):
    """
    AWS CLI tool must be installed and aws keys setup
    """
    source ="{0}/{1}".format(source_path,bag_name)
    s3_loc = "s3://{0}/{1}".format(s3_bucket,s3_location)
    log=open("{0}.tmp".format(task_id),"w+")
    status=call(['/env/bin/aws','s3','sync',source,s3_loc],stderr=log) 
    if status != 0:
        log.seek(0)
        msg= log.read()
        log.close()
        os.remove("{0}.tmp".format(task_id))
        self.update_state(state=states.FAILURE,meta=msg)
        raise Ignore()
    else:
        msg="Bag uploaded from {0} to {1}".format(source,s3_loc)
        
    return msg
  
