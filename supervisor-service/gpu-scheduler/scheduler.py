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
    
config = read_config()

GPU_demand = []
for tkc in config['tkc'][:1]:
    GPU_demand_item = {'tkcNamespace': tkc['tkcNamespace'], 'tkcName': tkc['tkcName'], 'pods': []}
    kubeconfig_path = get_tkc_secret(tkc)
    command = "kubectl get node --kubeconfig=" + kubeconfig_path
    process = subprocess.Popen(command.split(), stdout=subprocess.PIPE)
    output, error = process.communicate()
    with open(os.path.join(workdir, tkc['tkcNamespace'] + '-' + tkc['tkcName']), 'a') as f:
        f.write(output.decode())
    
    command = "kubectl get pods -A -o yaml --kubeconfig=" + kubeconfig_path
    process = subprocess.Popen(command.split(), stdout=subprocess.PIPE)
    output, error = process.communicate()
    pods = yaml.load(output.decode(), Loader=yaml.FullLoader)
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
                        GPU_demand_item['pods'].append(GPU_demand_pod_item)
    GPU_demand.append(GPU_demand_item)

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