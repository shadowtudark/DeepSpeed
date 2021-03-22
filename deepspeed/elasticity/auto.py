"""
Copyright 2021 The Microsoft DeepSpeed Team
"""

import logging
import threading
import time
import re
import os
import json
import time
import base64
import sys
import subprocess
import torch.distributed as dist
import signal
import os

from ..utils import logger

def auto_enabled(ds_config: dict):
    if 'IS_ELASTIC_TRAINING_JOB' in os.environ:
        if os.environ['IS_ELASTIC_TRAINING_JOB'].lower() == 'true':
            return True
    return False

def relaunch(state):
    relaunch_rank = state['relaunch_rank']
    
    if dist.get_rank() == relaunch_rank:
        cmd = os.environ['DS_CMD']
        cmd = base64.urlsafe_b64decode(cmd)
        cmd = json.loads(cmd)
        logger.info(f"deepspeed relaunching at rank:{relaunch_rank} with cmd = {cmd}")
        results = subprocess.Popen(cmd)
        logger.info(f"deepspeed relaunching at rank:{relaunch_rank} with cmd = {cmd}")
    
    logger.info(f"at rank:{dist.get_rank()}, finishing the program..")
    os.kill(os.getpid(), signal.SIGTERM)
    # does not work with threads
    #sys.exit(0)
                
def listen_for_changes(state):
    original_hostfile = open('/job/hostfile').read()
    original_hosts = set(re.findall("(worker-[0-9]+)", original_hostfile))

    #print(f"Running on {len(original_hosts)} nodes")
    #print("Original hostfile =", original_hostfile)
    ssh_config_file = os.environ['HOME'] + '/.ssh/config'

    interval = 5

    while True:
        # wait for some time
        sleep(interval)
        
        # read the file and check changes
        new_hostfile = open('/job/hostfile').read()
        new_hosts = set(re.findall("(worker-[0-9]+)", original_hostfile))

        config = open(ssh_config_file).read()
        config_hosts = set(re.findall("Host (worker-[0-9]+)", config))

        if config_hosts == new_hosts:
            if not len(new_hosts) == len(old_hosts):
                sorted_hosts = list(new_hosts)
                sorted_hosts.sort()
                state['relaunch_rank'] = int(sorted_hosts[0].split("-")[1])
                logger.info(f"Relaunch rank = {state['relaunch_rank']}")
                #time.sleep(1)
                if len(new_hosts) > len(old_hosts):
                    state['scale_up'] = True
                    # DeepSpeedEngine will read this and call relaunch
                    exit(0)
                elif len(new_hosts) < len(old_hosts):
                    state['scale_down'] = True
                    #print("\n_______________________________________________________\n")
                    #time.sleep(2)
                    relaunch(state)

        
# Unused but keeping it for now
def handle_scaling_event(state, old_hosts, config_file):
    new_hostfile = open('/job/hostfile').read()
    new_hosts = set(re.findall("(worker-[0-9]+)", new_hostfile))

    config = open(config_file).read()
    config_hosts = set(re.findall("Host (worker-[0-9]+)", config))

    #print(f"config_hosts={config_hosts}")
    #print(f"new_hosts={new_hosts}")
    #print(f"old_hosts={old_hosts}")
    
    if config_hosts == new_hosts:
        #print("sanity passed")
        if not len(new_hosts) == len(old_hosts):
            sorted_hosts = list(new_hosts)
            sorted_hosts.sort()
            state['relaunch_rank'] = int(sorted_hosts[0].split("-")[1])
            logger.info(f"Relaunch rank = {state['relaunch_rank']}")
            time.sleep(1)
            if len(new_hosts) > len(old_hosts):
                state['scale_up'] = True
                # DeepSpeedEngine will read this and call relaunch
            elif len(new_hosts) < len(old_hosts):
                state['scale_down'] = True
                print("\n_______________________________________________________\n")
                time.sleep(2)
                relaunch(state)

def listen_for_changes_with_inotify(state):
    import inotify
    import inotify.adapters
    original_hostfile = open('/job/hostfile').read()
    original_hosts = set(re.findall("(worker-[0-9]+)", original_hostfile))

    #print(f"Running on {len(original_hosts)} nodes")
    #print("Original hostfile =", original_hostfile)

    i = inotify.adapters.Inotify()

    ssh_config_path = os.environ['HOME'] + '/.ssh/'

    # Watch both directories
    i.add_watch('/job/')
    i.add_watch(ssh_config_path)

    for event in i.event_gen(yield_nones=False):
        (_, type_names, path, filename) = event
        print("PATH=[{}] FILENAME=[{}] EVENT_TYPES={}".format(path, filename, type_names))
        if filename == 'config' and type_names[0] == 'IN_MODIFY':
            #print("PATH=[{}] FILENAME=[{}] EVENT_TYPES={}".format(path, filename, type_names))
            state['config_changed'] = True

            if state['config_changed'] and state['hostfile_changed']:
                handle_scaling_event(state, original_hosts, ssh_config_path + 'config')

        if filename == 'hostfile' and type_names[0] == 'IN_MODIFY':
            #print("PATH=[{}] FILENAME=[{}] EVENT_TYPES={}".format(path, filename, type_names))
            state['hostfile_changed'] = True

            if state['hostfile_changed'] and state['config_changed']:
                handle_scaling_event(state, original_hosts, ssh_config_path + 'config')

def start_watching(state):
    x = threading.Thread(target=listen_for_changes, args=(state,), daemon=True)
    x.start()

# just for debugging -- deepspeed engine will do this
keep_training = True
step = 0

def train(state):
    global step    
    global keep_training

    print("Training ... step:", step)
    step += 1

    # actual training work
    time.sleep(2)

    # check if a scale up or scale down event has come and act accordingly
    if state['scale_up']:
        print(f"scaling up nodes, checkpointing, and restarting")
        keep_training = False

    if state['scale_down']:
        print(f"scaling down nodes and restarting")
        keep_training = False

if __name__ == "__main__":
    auto_state = {'scale_up': False, 'scale_down': False, 'config_changed': False, 'hostfile_changed': False}
    start_watching(auto_state)

    while keep_training:
        train(auto_state)

    logging.info("Main    : wait for the thread to finish")
    logging.info("Main    : all done")
