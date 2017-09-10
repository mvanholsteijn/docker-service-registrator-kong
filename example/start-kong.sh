#!/bin/bash
KONG_VERSION=0.11.0
docker pull postgres:9.4
docker pull kong:$KONG_VERSION

docker rm -f kong-database kong kong-dashboard kong-registrator

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
    -p 7946:7946 \
    kong:$KONG_VERSION kong start --vv

echo 'waiting for kong.'
while ! curl -o /dev/null http://localhost:8001/consumers ; do
	echo -n '.'
	sleep 1
done

docker run -d \
        --name kong-registrator \
	--restart unless-stopped \
        --link kong:kong \
	-v /var/run/docker.sock:/var/run/docker.sock \
	xebia/docker-service-registrator-kong:latest \
	--hostname mvanholsteijn.local \
	--admin-url https://kong:8444 \
	--no-verify-ssl \
        daemon

docker run -d  -P \
	--name kong-dashboard \
	--link kong:kong \
	--env SERVICE_NAME=kong-dashboard \
	--env KONG_API='{ "name": "kong-dashboard", 
			  "uris": ["/dashboard"], 
			  "strip_uri": true, 
			  "preserve_host": false }' \
	pgbi/kong-dashboard:v2
