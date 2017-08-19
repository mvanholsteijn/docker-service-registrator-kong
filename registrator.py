#!/usr/bin/env python 
import sys
import json
import logging
import click
import docker
import boto3
import socket
import requests
from botocore.exceptions import ClientError

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('KongServiceRegistrator')
log.setLevel('INFO')

class KongServiceRegistrator:
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
    def __init__(self, admin_url, dns_name, hostname):
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
	self.upstreams = {}
	self.targets = {}
        self.load()
        
        assert self.hostname == hostname        
        assert self.dns_name == dns_name        


    def sync_upstream(self, upstream, targets):
	in_kong = set(map(lambda t: t['target'], self.targets[upstream]))
	live = set(targets)
	to_delete = in_kong - live
	to_add = live - in_kong
	for target in to_delete:
		self.remove_target(upstream, target)
	for target in to_add:
		self.add_target(upstream, target)

    def load_upstreams(self):
	self.upstreams = {}
	next_page = '%s/upstreams?size=100' % self.admin_url
	while next_page:
		r = requests.get(next_page)
		if r.status_code == 200:
			response = r.json()
			next_page = response['next'] if 'next' in response else None
			for upstream in response['data']:
				self.upstreams[upstream['name']] = upstream
		elif r.status_code == 404:
			next_page = None
		else:
			log.error('failed to get upstreams at %s, %s' % (self.admin_url, r.text))
	self.upstreams = filter(lambda u : u.endswith(self.dns_name), self.upstreams)

    def load_targets(self, name):
	self.targets[name] = []
	r = requests.get('%s/upstreams/%s/targets/active' % (self.admin_url, name))
	if r.status_code == 200:
		response = r.json()
		owned_targets = filter(lambda t: t['target'].startswith('%s:' % self.hostname), response['data'])
		self.targets[name].extend(owned_targets)
	elif r.status_code == 404:
		next_page = None
	else:
		log.error('failed to get targets of %s at %s, %s' % (name, self.admin_url, r.text))

    def load(self):
	self.load_upstreams()
	for upstream in self.upstreams:
		self.load_targets(upstream)

    def add_upstream(self, name):
	if name not in self.upstreams:
		e = requests.post('%s/upstreams/' % self.admin_url, json={ 'name': name })
		if r.status_code == 409:
			r = requests.get('%s/upstreams/%s' %(self.admin_url, name))

		if r.status_code == 200 or r.status_code == 201:
			self.upstreams[name] = r.json()
		else:
			log.error('failed to add upstream %s at %s, status code %d, %s' % (name, self.admin_url, r.status_code, r.text))
	else:
		# upstream already exists
		pass

    def add_target(self, name, target):
	log.info('adding target %s to upstream %s' % (target, name))
	self.add_upstream(name)
	targets = self.targets[name]
	targets = filter(lambda t: t['target'] == target and t['weight'] != 0, targets)
	if len(targets) == 0:
		r = requests.post('%s/upstreams/%s/targets' % (self.admin_url, name), json={ 'target': target })
		if r.status_code == 200 or r.status_code == 201:
			self.targets[name].append(r.json())
		else:
			log.error('failed to add target %s to upstream %s at %s: %d, %s' % (target, name, self.admin_url, r.status_code, r.text))
	else:
		# target already exists
		pass
		
    def remove_target(self, name, target):
	log.info('removing target %s from upstream %s' % (target, name))
	url = '%s/upstreams/%s/targets/%s' % (self.admin_url, name, target)
	r = requests.delete(url)
	if r.status_code == 204:
		self.targets[name] = filter(lambda t: t['target'] != target, self.targets[name])
	else:
		log.error('failed to remove target %s to upstream %s at %s: %d, %s' % (target, name, url, r.status_code, r.text))
		

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

    def create_upstream_targets(self, container):
        """
        creates upstream targets for the container.
        that has a matching environment variable 'SERVICE_<port>_NAME'.
        If a single port is exposed, a matching SERVICE_NAME suffices.

        """
        result = {}
        env = self.get_environment_of_container(container)
        ports = container.attrs['NetworkSettings']['Ports']

        for port in ports:
            if ports[port] is None:
                # no ports exposed
                continue

            hostPort = ports[port][0]['HostPort']
            service_name = 'SERVICE_%s_NAME' % port.split('/')[0]
	    target = '%s:%s' % (self.hostname, hostPort)
	    upstream = None
            if service_name in env:
		upstream = '%s%s' % (env[service_name], self.dns_name)
            elif 'SERVICE_NAME' in env and len(ports) == 1:
		upstream = '%s%s' % (env['SERVICE_NAME'], self.dns_name)
            else:
                pass
	    if upstream is not None and upstream not in result:
		result[upstream] = target;
	    elif upstream is not None:
		log.warn('ignoring duplicate service name for port %s of container %s' % (port, container['ID']))

        return result

    def container_died(self, container_id, event):
	"""
        remove all invalid upstream targets. requires a full synchronization as we cannot link the 
	targets to the container id.
	"""
	self.sync() 

    def container_started(self, container_id, event):
        """
        create upstream targets for all exposed services of the specified container.
        """
        try:
            container = self.dockr.containers.get(container_id)
            targets = self.create_upstream_targets(container)
            if len(targets) > 0:
		for upstream in targets:
		    self.add_target(upstream, targets[upstream])
        except docker.errors.NotFound as e:
            log.error('container %s does not exist.' % container_id)

    def sync(self):
        """
	ensure that the upstream targets are 
	actually reflecting docker instances running on this host. 
        """
	targets = {upstream: [] for upstream in self.targets}
	containers = self.dockr.containers.list()
	for container in containers:
		container_targets  = self.create_upstream_targets(container)
		for upstream in container_targets:
			if upstream not in targets:
				targets[upstream] = []
			targets[upstream].append(container_targets[upstream])

	for upstream in targets:
		self.sync_upstream(upstream, targets[upstream])

    def remove_all(self):
        """
        remove all targets pointing to this host.
        """
	for upstream in self.targets:
		for target in self.targets[upstream]:
			self.remove_target(upstream, target['target'])

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
                        self.container_started(event['id'], event)
                    elif event['status'] == 'die':
                        self.container_died(event['id'], event)
                    else:
                        log.debug('skipping event %s' % event['status'])
                else:
                    pass  # boring...

    
@click.group()
@click.option('--dns-name', required=False, default='.docker.internal', help='to append to the service name')
@click.option('--hostname', required=False, default=socket.getfqdn(), help='to use in target records.')
@click.option('--admin-url', required=False, default='http://localhost:8001', help='of the Kong Admin API')
@click.pass_context
def cli(ctx, dns_name, hostname, admin_url):
    e = KongServiceRegistrator(admin_url, dns_name, hostname)
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
