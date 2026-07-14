from app.context.collectors.compose import DockerComposeCollector
from app.context.collectors.kubernetes import KubernetesCollector
from app.context.collectors.nginx import NginxCollector
from app.context.collectors.project_file import ProjectFileCollector
from app.context.collectors.systemd import SystemdCollector


def collectors_for(environment) -> list:
    collectors = []
    if environment.runtime_type == "docker_compose": collectors.append(DockerComposeCollector())
    elif environment.runtime_type == "kubernetes": collectors.append(KubernetesCollector())
    elif environment.runtime_type == "systemd": collectors.append(SystemdCollector())
    if environment.config_json.get("context_files"): collectors.append(ProjectFileCollector())
    if environment.config_json.get("nginx_config_files"): collectors.append(NginxCollector())
    return collectors

