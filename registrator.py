#!/usr/bin/env python
"""
    manages the upstream targets in Kong for Docker containers running on this host.

    When a container is started, the registrator will create a upstream target for each
    exposed port which has a matching SERVICE_<exposed-port>_NAME environment
    variable. If the container exposes a single port, it suffices to have a SERVICE_NAME
    environment variable.

    The registrator has three commands: 'remove_all', 'sync' and 'daemon'.

        remove_all  - remove all targets pointing to this host
        sync        - synchronise the targets  with the running containers
        daemon      - continuously update targets by subscribing to the Docker event stream

"""
import socket
import json
import logging
import click
import docker
import requests
from jsondiff import diff
import urllib3

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('KongServiceRegistrator')
log.setLevel('INFO')

#
# disable warnings on ssl usage (really irritating if you explicitly specify verify_ssl = False)
#
urllib3.disable_warnings()


class KongServiceRegistrator(object):

    def __init__(self, admin_url, dns_name, hostname, verify_ssl):
        """
        constructor.
        """
        assert dns_name is not None
        assert hostname is not None
        assert admin_url is not None

        self.dockr = docker.from_env()
        self.hostname = hostname
        self.dns_name = dns_name
        self.admin_url = admin_url
        self.verify_ssl = verify_ssl
        self.upstreams = {}
        self.targets = {}
        self.apis = {}
        self.kong_version = [0, 11, 0]
        self.below_v_0_11 = False

        self.load()

        assert self.hostname == hostname
        assert self.dns_name == dns_name

    def get_kong_version(self):
        """
        get the version of the Kong API, and set self.kong_version and self.below_v_0_11.
        """
        info = {}
        r = requests.get(self.admin_url)
        if r.status_code == 200:
            info = r.json()
        else:
            log.error('Failed to get Kong API information from %s, %d, %s',
                      r.url, r.status_code, r.text)

        v = info['version'] if 'version' in info else '0.11.0'
        self.kong_version = [int(n) for n in v]
        self.below_v_0_11 = self.kong_version[
            0] == 0 and self.kong_version[1] < 11

    def sync_upstream(self, upstream, targets):
        """
        synchronize all upstream targets on this machine with the targets registerted in Kong
        """
        live = set(targets)
        in_kong = set(map(lambda t: t['target'], self.targets[
                      upstream])) if upstream in self.targets else set()
        to_delete = in_kong - live
        to_add = live - in_kong
        for target in to_delete:
            self.remove_targets(upstream, target)
        for target in to_add:
            self.add_target(upstream, target)

    def load_apis(self):
        """
        load all current API definition from Kong into self.apis
        """
        self.apis = {}
        next_page = '%s/apis?size=100' % self.admin_url
        while next_page:
            r = requests.get(next_page, verify=self.verify_ssl)
            if r.status_code == 200:
                response = r.json()
                next_page = response['next'] if 'next' in response else None
                for api in response['data']:
                    self.apis[api['name']] = api
            elif r.status_code == 404:
                next_page = None
            else:
                log.error('failed to get apis at %s, %s',
                          self.admin_url, r.text)

    def load_upstreams(self):
        """
        load all upstreams from Kong into self.upstreams
        """
        self.upstreams = {}
        next_page = '%s/upstreams?size=100' % self.admin_url
        while next_page:
            r = requests.get(next_page, verify=self.verify_ssl)
            if r.status_code == 200:
                response = r.json()
                next_page = response['next'] if 'next' in response else None
                for upstream in response['data']:
                    self.upstreams[upstream['name']] = upstream
            elif r.status_code == 404:
                next_page = None
            else:
                log.error('failed to get upstreams at %s, %s',
                          self.admin_url, r.text)
        self.upstreams = filter(lambda u: u.endswith(
            self.dns_name), self.upstreams)

    def load_targets(self, name):
        """
        load all targets pointing to `self.hostname` for the upstream 'name'.
        """
        self.targets[name] = []
        if self.below_v_0_11:
            url = '%s/upstreams/%s/targets/active' % (self.admin_url, name)
        else:
            url = '%s/upstreams/%s/targets' % (self.admin_url, name)
        r = requests.get(url, verify=self.verify_ssl)
        if r.status_code == 200:
            response = r.json()
            owned_targets = filter(lambda t: t['target'].startswith(
                '%s:' % self.hostname), response['data'])
            self.targets[name].extend(owned_targets)
        elif r.status_code == 404:
            pass  # no targets yet..
        else:
            log.error('failed to get targets of %s at %s, %d, %s',
                      name, r.url, r.status_code, r.text)

    def load(self):
        """
        load all upstream targets from Kong.
        """
        self.load_upstreams()
        for upstream in self.upstreams:
            self.load_targets(upstream)

    def add_upstream(self, name):
        """
        add the upstream `name' to Kong.
        """
        if name not in self.upstreams:
            r = requests.post(
                '%s/upstreams/' % self.admin_url, json={'name': name},
                verify=self.verify_ssl)
            if r.status_code == 409:
                r = requests.get(
                    '%s/upstreams/%s' % (self.admin_url, name),
                    verify=self.verify_ssl)

            if r.status_code == 200 or r.status_code == 201:
                self.upstreams[name] = r.json()
            else:
                log.error(
                    'failed to add upstream %s at %s, status code %d, %s',
                    name, r.url, r.status_code, r.text)
        else:
            # upstream already exists
            pass

    def add_target(self, name, target):
        """
        add the target `target` to the upstream `name` in Kong.
        """
        log.info('adding target %s to upstream %s', target, name)
        self.add_upstream(name)
        targets = self.targets[name]
        targets = filter(lambda t: t['target'] ==
                         target and t['weight'] != 0, targets)
        if len(targets) == 0:
            r = requests.post('%s/upstreams/%s/targets' %
                              (self.admin_url, name),
                              json={'target': target},
                              verify=self.verify_ssl)
            if r.status_code == 200 or r.status_code == 201:
                self.targets[name].append(r.json())
            else:
                log.error(
                    'failed to add target %s to upstream %s at %s: %d, %s',
                    target, name, self.admin_url, r.status_code, r.text)
        else:
            # target already exists
            log.debug(
                'target "%s" for upstream "%s" is already registered', target,
                name)
            pass

    def remove_targets(self, upstream, target):
        """
        remove all targets from the upstream. Kong sometimes creates multiple logical targets
        """
        target_ids = set(map(lambda t: t['id'], filter(
            lambda t: t['target'] == target and t['weight'] > 0, self.targets[upstream])))
        for target_id in target_ids:
            self.remove_target(upstream, target, target_id)

    def remove_target(self, name, target, target_id):
        """
        remove the target `target` from the upstream `name` in Kong.
        """
        log.info('removing target %s (%s) from upstream %s',
                 target, target_id, name)
        url = '%s/upstreams/%s/targets/%s' % (self.admin_url, name, target_id)
        r = requests.delete(url, verify=self.verify_ssl)
        if r.status_code != 204:
            log.error(
                'failed to remove target %s from upstream %s at %s: %d, %s',
                target, name, r.url, r.status_code, r.text)

        self.targets[name] = filter(
            lambda t: t['id'] != target_id, self.targets[name])

    def get_environment_of_container(self, container):
        """
        returns the environment variables of the container as a dictionary.
        """
        assert container is not None

        result = {}
        env = container.attrs['Config']['Env']
        for e in env:
            parameter = e.split('=', 1)
            result[parameter[0]] = parameter[1]

        assert len(env) == len(container.attrs['Config']['Env'])

        return result

    def sync_apis(self, apis):
        """
        synchronizes the API definition defined on this machine with Kong.
        """
        self.load_apis()
        for name in apis:
            definition = apis[name]
            if name in self.apis:
                                # api with the same name already exists, check
                                # for update
                current = self.apis[name]
                differences = diff(current, definition, syntax='explicit')
                if '$update' in differences and len(
                        differences['$update']) > 0:
                    log.info('updating API definition %s.', name)
                    r = requests.patch(
                        '%s/apis/%s' % (self.admin_url, name),
                        json=definition, verify=self.verify_ssl)
                    if r.status_code == 200 or r.status_code == 201:
                        self.apis[name] = r.json()
                    else:
                        log.error('failed to update %s at %s, %s',
                                  name, self.admin_url, r.text)
                else:
                    log.debug('API definition %s is up-to-date.', name)
            else:
                log.info('creating API definition %s.', name)
                r = requests.put('%s/apis/' % self.admin_url,
                                 json=definition, verify=self.verify_ssl)
                if r.status_code == 200 or r.status_code == 201:
                    self.apis[name] = r.json()
                else:
                    log.error('failed to create %s at %s, %s',
                              name, self.admin_url, r.text)

    def get_all_exposed_tcp_ports(self, container):
        """
        returns all TCP ports exposed by `container`.
        """
        ports = container.attrs['NetworkSettings']['Ports']
        return dict((k, v) for k, v in ports.items()
                    if k.endswith('/tcp') and v is not None)

    def get_all_tcp_ports(self, container):
        """
        returns all publishable TCP ports by `container`.
        """
        ports = container.attrs['NetworkSettings']['Ports']
        return dict((k, v) for k, v in ports.items() if k.endswith('/tcp'))

    def get_environment_value_for_port(self, container, prefix, postfix, port):
        """
        gets the environment variable for `prefix`_`port.split('/')[0]`_`postfix` or
        for `prefix`_`postfix if the number of exposed ports == 1.

        if no such environment variable exists, None is returned.
        """
        env = self.get_environment_of_container(container)
        tcp_ports = self.get_all_tcp_ports(container)

        full_name = '%s_%s' % (prefix, postfix)
        port_name = '%s_%s_%s' % (prefix, port.split('/')[0], postfix)
        env = self.get_environment_of_container(container)

        if port_name in env:
            return env[port_name]

        if full_name in env and len(tcp_ports) == 1:
            return env[full_name]

        return None

    def get_service_name_for_port(self, container, port):
        """
        get the value of the SERVICE_NAME environment variable for the specified `port`.
        """
        return self.get_environment_value_for_port(
            container, 'SERVICE', 'NAME', port)

    def get_kong_api_for_port(self, container, port):
        """
        get the value of the KONG_API environment variable for the specified `port`.
        """
        return self.get_environment_value_for_port(
            container, 'KONG', 'API', port)

    def get_api_definitions(self, container):
        """
        gets the Kong API definitions for the container.

        the API definition is specified through the Port environment variable
        KONG_API.

        to avoid duplicate definitions, the fields `name` and `upstream_url` may
        be omitted if a SERVICE_NAME environment variable has been specified for
        the port. In that case, the name of the API will be the SERVICE_NAME,
        while the  upstream_url will be set to http://<service_name><self.dns_name>'.
        """
        result = {}
        ports = self.get_all_exposed_tcp_ports(container)

        for port in ports:
            api_definition = self.get_kong_api_for_port(container, port)

            if api_definition is None:
                continue

            service_name = self.get_service_name_for_port(container, port)
            upstream = 'http://%s%s' % (service_name,
                                        self.dns_name) if service_name is not None else None

            try:
                api_definition = json.loads(api_definition)
                if 'upstream_url' not in api_definition and upstream is not None:
                    api_definition['upstream_url'] = upstream
                if 'name' not in api_definition and service_name is not None:
                    api_definition['name'] = service_name
            except ValueError as e:
                log.error(
                    'invalid KONG API definition for port %s of container %s, %s',
                    port, container['ID'],
                    e.message)
                continue

            if 'name' not in api_definition:
                log.error(
                    'name field missing missing in API definition for port %s of container %s',
                    port, container['ID'])
                continue

            name = api_definition['name']
            if name not in result:
                result[name] = api_definition
            else:
                log.error(
                    'ignoring duplicate API definition for port %s of container %s',
                    port, container['ID'])

        return result

    def get_upstream_targets(self, container):
        """
        get Kong upstream targets definition for the container.

        for each exposed port which has a SERVICE_NAME specified a
        entry will be added to the returned dictionary.

            "<SERVICE_NAME>.`self.dns_name`" : [ "`self.hostname`:<exposed-port>" ]

        duplicate service names are not allowed.
        """
        result = {}
        ports = self.get_all_exposed_tcp_ports(container)

        for port in ports:
            service_name = self.get_service_name_for_port(container, port)
            if service_name is None:
                continue

            hostPort = ports[port][0]['HostPort']
            target = '%s:%s' % (self.hostname, hostPort)
            upstream = '%s%s' % (service_name, self.dns_name)
            if upstream not in result:
                result[upstream] = target
            elif upstream is not None:
                log.warn(
                    'ignoring duplicate service name for port %s of container %s',
                    port, container['ID'])

        return result

    def container_died(self):
        """
        remove all invalid upstream targets. requires a full synchronization as we cannot link the
        targets to the container id.
        """
        self.sync()

    def container_started(self, container_id):
        """
        create upstream targets for all exposed services of the specified container.
        """
        try:
            container = self.dockr.containers.get(container_id)
            targets = self.get_upstream_targets(container)
            if len(targets) > 0:
                for upstream in targets:
                    self.add_target(upstream, targets[upstream])

            apis = self.get_api_definitions(container)
            self.sync_apis(apis)

        except docker.errors.NotFound:
            log.error('container %s does not exist.', container_id)

    def sync(self):
        """
        ensure that the upstream targets are
        actually reflecting docker instances running on this host.
        """
        targets = {upstream: [] for upstream in self.targets}
        apis = {}
        containers = self.dockr.containers.list()
        for container in containers:
            container_targets = self.get_upstream_targets(container)
            for upstream in container_targets:
                if upstream not in targets:
                    targets[upstream] = []
                targets[upstream].append(container_targets[upstream])

            container_apis = self.get_api_definitions(container)
            apis.update(container_apis)

        for upstream in targets:
            self.sync_upstream(upstream, targets[upstream])
        self.sync_apis(apis)

    def remove_all(self):
        """
        remove all targets pointing to this host.
        """
        for upstream in self.targets:
            for target in self.targets[upstream]:
                self.remove_target(upstream, target['target'], target['id'])

    def process_events(self):
        """
        Process docker container start and die events.
        """
        for e in self.dockr.events():
            lines = filter(lambda l: len(l) > 0, e.split('\n'))
            for line in lines:
                event = json.loads(line)
                if event['Type'] == 'container':
                    if event['status'] == 'start':
                        self.container_started(event['id'])
                    elif event['status'] == 'die':
                        self.container_died()
                    else:
                        log.debug('skipping event %s', event['status'])
                else:
                    pass  # boring...


@click.group()
@click.option(
    '--dns-name', required=False, default='.docker.internal',
    help='to append to the service name')
@click.option(
    '--hostname', required=False, default=socket.getfqdn(),
    help='to use in target records.')
@click.option(
    '--admin-url', required=False, default='http://localhost:8001',
    help='of the Kong Admin API')
@click.option(
    '--verify-ssl/--no-verify-ssl', required=False, default=True,
    help='verify ssl connection to Kong Admin API')
@click.pass_context
def cli(ctx, dns_name, hostname, admin_url, verify_ssl):
    e = KongServiceRegistrator(admin_url, dns_name, hostname, verify_ssl)
    ctx.obj['registrator'] = e


@cli.command()
@click.pass_context
def daemon(ctx):
    """
    process docker container 'start' and 'die' events to add and delete upstream targets accordingly.
    """
    e = ctx.obj['registrator']
    e.sync()
    e.process_events()


@cli.command()
@click.pass_context
def remove_all(ctx):
    """
    remove all upstream targets associated with this host.
    """
    e = ctx.obj['registrator']
    e.remove_all()


@cli.command()
@click.pass_context
def sync(ctx):
    """
    Synchronize the upstream targets with the current docker containers.
    """
    e = ctx.obj['registrator']
    e.sync()

if __name__ == '__main__':
    cli(obj={})
