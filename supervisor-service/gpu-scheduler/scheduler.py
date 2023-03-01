#!/usr/bin/env python

import functools
import yaml
import base64
import os
import subprocess
import re

workdir='/app'

def read_config():
    config_path="/etc/config/gpu-scheduler-config.yaml"
    with open(config_path, "r") as stream:
        try:
            return yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            print(exc)

def get_tkc_secret(tkc):
    kubeconfig_path = os.path.join(workdir, 'kubeconfig.yaml')
    command = "kubectl get secret {}-kubeconfig -n {}".format(tkc['tkcName'], tkc['tkcNamespace']) + " -o jsonpath='{.data.value}'"
    process = subprocess.Popen(command.split(), stdout=subprocess.PIPE)
    output, error = process.communicate()
    base64.b64decode(output)
    with open(kubeconfig_path, 'a') as f:
        f.write(base64.b64decode(output).decode())
    return kubeconfig_path

def query_gcdm():
    command = os.path.join(workdir, 'dcgm_query.sh')
    process = subprocess.Popen(command.split(), stdout=subprocess.PIPE)
    output, error = process.communicate()
    output_array = output.decode().split("\n")
    gcdm_info=[]
    for line in output_array:
        if line.startswith("DCGM_FI_DEV_SM_CLOCK"):
            print(line)
            regexp = re.compile(r'DCGM_FI_DEV_SM_CLOCK{gpu="(?P<gpu>.*?)",UUID="(?P<UUID>.*?)",device="(?P<device>.*?)",modelName="(?P<modelName>.*?)",Hostname="(?P<Hostname>.*?)",DCGM_FI_DRIVER_VERSION="(?P<DCGM_FI_DRIVER_VERSION>.*?)",container="(?P<container>.*?)",namespace="(?P<namespace>.*?)",pod="(?P<pod>.*?)"}.*')
            re_match = regexp.match(line)
            re_match.groups()
            gcdm_info.append({
                "gpu":  re_match.group("gpu"),
                "UUID":  re_match.group("UUID"),
                "device":  re_match.group("device"),
                "modelName":  re_match.group("modelName"),
                "Hostname":  re_match.group("Hostname"),
                "DCGM_FI_DRIVER_VERSION":  re_match.group("DCGM_FI_DRIVER_VERSION"),
                "container":  re_match.group("container"),
                "namespace":  re_match.group("namespace"),
                "pod":  re_match.group("pod"),
            })
    return gcdm_info

def query_gpu_demand():
    command = "kubectl get pods -A -o yaml --kubeconfig=" + kubeconfig_path
    process = subprocess.Popen(command.split(), stdout=subprocess.PIPE)
    output, error = process.communicate()
    pods = yaml.load(output.decode(), Loader=yaml.FullLoader)
    GPU_demand_item = []
    for index, pod in enumerate(pods['items']):
        if pod['status']['phase'] == "Pending":
            print(index)
            for condition in pod['status']['conditions']:
                if condition['type'] == 'PodScheduled' and condition['status'] == 'False' and condition['reason'] == 'Unschedulable' and \
                    re.search("0/[0-9]+ nodes are available: [0-9]+ Insufficient nvidia.com/gpu", condition['message']):
                        GPU_demand_pod_item = {}
                        GPU_demand_pod_item['namespace'] = pod['metadata']['namespace']
                        GPU_demand_pod_item['name'] = pod['metadata']['name']
                        GPU_demand_pod_item['GPU_product'] = pod['spec']['nodeSelector']['nvidia.com/gpu.product']
                        if len(pod['spec']['containers']) >= 2:
                            GPU_demand_pod_item['GPU_count'] = functools.reduce(lambda a, b: int(a['resources']['limits']['nvidia.com/gpu']) + int(b['resources']['limits']['nvidia.com/gpu']), pod['spec']['containers'])
                        else:
                            GPU_demand_pod_item['GPU_count'] = pod['spec']['containers'][0]['resources']['limits']['nvidia.com/gpu']
                        GPU_demand_item.append(GPU_demand_pod_item)
    return GPU_demand_item

config = read_config()

for tkc in config['tkc'][:1]:
    GPU_demand_item = {'tkcNamespace': tkc['tkcNamespace'], 'tkcName': tkc['tkcName'], 'pods': []}
    kubeconfig_path = get_tkc_secret(tkc)
    command = "kubectl get node --kubeconfig=" + kubeconfig_path
    process = subprocess.Popen(command.split(), stdout=subprocess.PIPE)
    output, error = process.communicate()
    with open(os.path.join(workdir, tkc['tkcNamespace'] + '-' + tkc['tkcName']), 'a') as f:
        f.write(output.decode())
    
    GPU_demand = query_gpu_demand()
    gcdm_info = query_gcdm()


    
    


# kubectl get pods -o=jsonpath='{.items[?(@.status.phase=="Pending"&&@.status.qosClass=="BestEffort")]}'

        
# status:
#   conditions:
#   - lastProbeTime: null
#     lastTransitionTime: "2023-02-27T07:38:43Z"
#     message: '0/2 nodes are available: 1 Insufficient nvidia.com/gpu, 1 node(s) had
#       taint {node-role.kubernetes.io/master: }, that the pod didn''t tolerate.'
#     reason: Unschedulable
#     status: "False"
#     type: PodScheduled
#   phase: Pending
#   qosClass: BestEffort