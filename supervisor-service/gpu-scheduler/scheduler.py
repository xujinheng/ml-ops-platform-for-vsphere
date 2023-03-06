#!/usr/bin/env python

import functools
import yaml
import base64
import os
import subprocess
import re
import time

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

def query_gpu_demand(kubeconfig_path):
    command = "kubectl get pods -A -o yaml --kubeconfig=" + kubeconfig_path
    process = subprocess.Popen(command.split(), stdout=subprocess.PIPE)
    output, error = process.communicate()
    pods = yaml.load(output.decode(), Loader=yaml.FullLoader)
    GPU_demand = []
    for index, pod in enumerate(pods['items']):
        if pod['status']['phase'] == "Pending" and "gpu-scheduled" not in pod['metadata']['annotations']:
            for condition in pod['status']['conditions']:
                if condition['type'] == 'PodScheduled' and condition['status'] == 'False' and condition['reason'] == 'Unschedulable' and \
                    re.search("0/[0-9]+ nodes are available:.*?[0-9]+ Insufficient nvidia.com/gpu", condition['message']):
                        GPU_demand_pod_item = {}
                        GPU_demand_pod_item['namespace'] = pod['metadata']['namespace']
                        GPU_demand_pod_item['name'] = pod['metadata']['name']
                        GPU_demand_pod_item['GPU_product'] = pod['spec']['nodeSelector']['nvidia.com/gpu.product']
                        if len(pod['spec']['containers']) >= 2:
                            GPU_demand_pod_item['GPU_count'] = functools.reduce(lambda a, b: int(a['resources']['limits']['nvidia.com/gpu']) + int(b['resources']['limits']['nvidia.com/gpu']), pod['spec']['containers'])
                        else:
                            GPU_demand_pod_item['GPU_count'] = pod['spec']['containers'][0]['resources']['limits']['nvidia.com/gpu']
                        GPU_demand.append(GPU_demand_pod_item)
    return GPU_demand

def compute_GPU_node_demand(GPU_demand, GPU_supply):
    if len(GPU_demand) == 0:
        return None
    GPU_node_demand = {} 
    a = [{'namespace': 'tkc-workload', 'name': 'nvidia-plugin-test-dff8ffc95-qrm2h', 'GPU_product': 'GRID-V100-4C', 'GPU_count': '1'}, {'namespace': 'tkc-workload', 'name': 'nvidia-plugin-test-dff8ffc95-qrm2h', 'GPU_product': 'GRID-V100-4C', 'GPU_count': '3'}, {'namespace': 'tkc-workload', 'name': 'nvidia-plugin-test-dff8ffc95-qrm2h', 'GPU_product': 'GRID-V100-8C', 'GPU_count': '2'}]
    for gpu_demand_item in GPU_demand:
        if gpu_demand_item['GPU_product'] in GPU_node_demand:
            GPU_node_demand[gpu_demand_item['GPU_product']] += int(gpu_demand_item['GPU_count'])
        else:
            GPU_node_demand[gpu_demand_item['GPU_product']] = int(gpu_demand_item['GPU_count'])
    return GPU_node_demand

def patch_tkc(GPU_product, GPU_count, tkc):
        GPU_product_map = {'GRID-V100-4C': 'vgpu-v100-4c', 
                           'GRID-V100-8C': 'vgpu-v100-8c',
                        }
        command = "kubectl get tkc {} -n {} -o yaml".format(tkc['tkcName'], tkc['tkcNamespace'])
        process = subprocess.Popen(command.split(), stdout=subprocess.PIPE)
        output, error = process.communicate()
        tkc_yaml = yaml.load(output.decode(), Loader=yaml.FullLoader)
        nodePool_template = {
            "name": "np-" + GPU_product_map[GPU_product],
            "replicas": GPU_count,
            "storageClass": "k8s-storage-policy",
            "tkr": {"reference": {"name": "v1.23.8---vmware.2-tkg.2-zshippable"}},
            "vmClass": GPU_product_map[GPU_product],
            "volumes":[
                {"capacity": {"storage": "70Gi"}, "mountPath": "/var/lib/containerd", "name": "containerd"},
                {"capacity": {"storage": "70Gi"}, "mountPath": "/var/lib/kubelet", "name": "kubelet"},
            ]
        }
        while list(filter(lambda d: d['name'] == nodePool_template['name'], tkc_yaml['spec']['topology']['nodePools'])):
            nodePool_template['name'] = nodePool_template['name'] + "1"
        tkc_yaml['spec']['topology']['nodePools'].append(nodePool_template)
        tkc_yaml_patch = {
            "spec": {
                "topology": {
                    "nodePools": tkc_yaml['spec']['topology']['nodePools']
                }
            }
        }
        with open(os.path.join(workdir, 'tkc-patch-file.yaml'), 'w') as f:
            yaml.dump(tkc_yaml_patch, f, default_flow_style=False, allow_unicode=True)
        command = "kubectl patch tkc {} -n {} --type merge --patch-file {}".format(tkc['tkcName'], tkc['tkcNamespace'], os.path.join(workdir, "tkc-patch-file.yaml"))
        process = subprocess.Popen(command.split(), stdout=subprocess.PIPE)
        output, error = process.communicate()
        return output

def annotate_pod(GPU_demand, kubeconfig_path):
    for GPU_demand_item in GPU_demand:
        command = "kubectl annotate pod {} -n {} gpu-scheduled=True --kubeconfig={}".format(GPU_demand_item['name'], GPU_demand_item['namespace'], kubeconfig_path)
        process = subprocess.Popen(command.split(), stdout=subprocess.PIPE)
        output, error = process.communicate()
        print(output, error)

config = read_config()
print("Read Config file:")
print(config)
print("\n\n")

while True:
    print("\n" + "=" * 30)
    print('%s'%time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())) 
    for tkc in config['tkc']:
        print("\n" + "-" * 15)
        print("1. Get kubeconfig for tkc {} -n {}".format(tkc["tkcName"], tkc["tkcNamespace"]))
        kubeconfig_path = get_tkc_secret(tkc)
        print("2. Get GPU demand")
        GPU_demand = query_gpu_demand(kubeconfig_path)
        print(GPU_demand)
        print("3. Get GPU GPU_supply")
        GPU_supply = query_gcdm()
        print(GPU_supply)
        # Assumption: GPU_count for GPU_demand is always 1, which leads to two cases
        # #1: GPU_demands is empty list, nothing to do
        # #2: GPU_demands is not empty, while GPU_supply is fully used.
        print("4. Compute GPU node demand")
        GPU_node_demand = compute_GPU_node_demand(GPU_demand, GPU_supply)
        print(GPU_node_demand)
        if GPU_node_demand:
            print("* GPU_node_demand exist:")
            print("5. Patch tkc")
            for GPU_product, GPU_count in GPU_node_demand.items():
                print("Add {} {} for tkc {} -n {}".format(GPU_count, GPU_product, tkc["tkcName"], tkc["tkcNamespace"]))
                output = patch_tkc(GPU_product, GPU_count, tkc)
                print(output)
            print("6. Annotate gpu-scheduled=True to scheduled pods")
            print(GPU_demand)
            annotate_pod(GPU_demand, kubeconfig_path)
        else:
            print("* GPU_node_demand is empty, nothing to do")
    print("sleep for 30sec")
    time.sleep(30)
    

# kubectl get pods -o=jsonpath='{.items[?(@.status.phase=="Pending"&&@.status.qosClass=="BestEffort")]}'

# status:
#   conditions:
#   - lastProbeTime: null
#     lastTransitionTime: "2023-03-01T10:18:40Z"
#     message: '0/3 nodes are available: 1 node(s) had taint {node-role.kubernetes.io/master:
#       }, that the pod didn''t tolerate, 2 Insufficient nvidia.com/gpu.'
#     reason: Unschedulable
#     status: "False"
#     type: PodScheduled
#   phase: Pending
#   qosClass: BestEffort

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