#!/bin/bash
KONG_VERSION=0.10.3
docker pull postgres:9.4
docker pull kong:$KONG_VERSION
docker pull xebia/docker-service-registrator-kong:latest
docker pull mvanholsteijn/paas-monitor:latest

docker rm -f kong-database kong 
docker run -d --name kong-database \
              -p 5432:5432 \
              -e POSTGRES_USER=kong \
              -e POSTGRES_DB=kong \
              postgres:9.4

echo 'waiting for postgres.'
while ! docker exec -i -e PGPASSWORD=kong kong-database psql --host localhost --user kong < /dev/null > /dev/null 2>&1; do
	echo -n '.'
	sleep 1
done
echo

docker run -it --rm \
    --link kong-database:kong-database \
    -e KONG_DATABASE=postgres \
    -e KONG_PG_HOST=kong-database \
    -e KONG_CASSANDRA_CONTACT_POINTS=kong-database \
    kong:$KONG_VERSION kong migrations up

docker run -d --name kong \
    --link kong-database:kong-database \
    -e KONG_DATABASE=postgres \
    -e KONG_PG_HOST=kong-database \
    -e KONG_CASSANDRA_CONTACT_POINTS=kong-database \
    -p 8000:8000 \
    -p 8443:8443 \
    -p 8001:8001 \
    -p 8444:8444 \
    kong:$KONG_VERSION

echo 'waiting for kong.'
while ! curl -o /dev/null http://localhost:8001/consumers ; do
	echo -n '.'
	sleep 1
done

#docker run -v $PWD/config.yml:/config.yml \
        #--link kong:kong \
        #xebia/kongfig \
        #--path /config.yml \
        #--host kong:8001

docker run -d \
	--restart unless-stopped \
        --link kong:kong \
	-v /var/run/docker.sock:/var/run/docker.sock \
	xebia/docker-service-registrator-kong:latest \
	--hostname mvanholsteijn.local \
	--admin-url https://kong:8444 \
	--no-verify-ssl \
        daemon

for i in {1..4}; do
	docker run -d  -P \
		--env SERVICE_NAME=paas-monitor \
		--env KONG_API='{ "name": "paas-monitor", 
				  "upstream_url": "http://paas-monitor.docker.internal", 
				  "uris": ["/paas-monitor"], 
				  "strip_uri": true, 
				  "preserve_host": false }' \
		mvanholsteijn/paas-monitor:latest
done

open http://localhost:8000/paas-monitor/
