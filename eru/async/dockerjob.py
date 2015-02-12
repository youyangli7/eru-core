# coding: utf

from io import BytesIO
from itertools import chain

from res.ext.common import random_string

from eru.common import settings
from eru.common.clients import get_docker_client


DOCKER_FILE_TEMPLATE = '''
FROM {base}
ENV NBE 1
RUN git clone {git_url} /{appname}
WORKDIR /{appname}
RUN git reset --hard {sha1}
RUN {build_cmd}
'''


def build_image(host, version, base):
    # TODO use context_dir
    """
    用 host 机器, 以 base 为基础镜像, 为 version 构建
    一个稍后可以运行的镜像.
    """
    client = get_docker_client(host)
    appname = version.appconfig.appname
    build_cmd = version.appconfig.build
    repo = '{0}/{1}'.format(settings.DOCKER_REGISTRY, appname)
    tag = '{0}:{1}'.format(repo, version.short_sha)

    dockerfile = BytesIO(
        DOCKER_FILE_TEMPLATE.format(
            base=base, git_url=version.app.git,
            appname=appname, sha1=version.sha, build_cmd=build_cmd
        )
    )

    build_gen = client.build(fileobj=dockerfile, rm=True, tag=tag)
    push_gen = client.push(repo, tag=version.sha[:7], stream=True, insecure_registry=True)
    return chain(build_gen, push_gen)


def create_containers(host, version, entrypoint, env, ncontainer, cores=[], ports=[], daemon=False):
    # TODO now daemon is not actually used
    """
    在 host 机器上, 用 entrypoint 在 env 下运行 ncontainer 个容器.
    这些容器可能占用 cores 这些核, 以及 ports 这些端口.
    daemon 用来指定这些容器的监控方式, 暂时没有用.
    """
    client = get_docker_client(host)
    appconfig = version.appconfig
    resconfig = version.get_resource_config(env)

    appname = appconfig.appname
    image = '{0}/{1}:{2}'.format(settings.DOCKER_REGISTRY, appname, version.short_sha)
    cmd = appconfig.entrypoints[entrypoint]

    # build name
    # {appname}_{entrypoint}_{ident_id}
    container_name = '_'.join([appname, entrypoint, random_string(6)])

    env = {
        'NBE_RUNENV': env.upper(),
        'NBE_POD': host.pod.name,
        'NBE_PERMDIR': settings.NBE_CONTAINER_PERMDIR % appname,
    }
    env.update(resconfig.to_env_dict(appname))

    volumes = [settings.NBE_CONTAINER_PERMDIR % appname, ]
    user = version.app_id # 可以控制从多少开始
    working_dir = '/%s' % appname
    ports = [appconfig.port, ] if appconfig.port else None

    containers = []
    cores_per_container = len(cores) / ncontainer
    for index in xrange(ncontainer):
        used_cores = cores[index*cores_per_container:(index+1)*cores_per_container].label if cores else ''
        cpuset = ','.join([c.label for c in used_cores])
        container = client.create_container(image=image, command=cmd, user=user, environment=env,
                volumes=volumes, name=container_name, cpuset=cpuset, working_dir=working_dir, ports=ports)
        container_id = container['Id']

        # start options
        # port binding and volume binding
        port = ports[index]
        port_bindings = {appconfig.port: port.port} if ports else None
        binds = {settings.NBE_HOST_PERMDIR % appname: {'bind': settings.NBE_CONTAINER_PERMDIR % appname, 'ro': False}}
        client.start(container=container_id, port_bindings=port_bindings, binds=binds)
        
        containers.append((container_id, container_name, entrypoint, used_cores, port))
    return containers
