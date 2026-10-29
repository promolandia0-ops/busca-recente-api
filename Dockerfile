FROM searxng/searxng:latest
COPY settings.yml /etc/searxng/settings.yml
COPY api.py start.sh /app/
USER root
RUN chmod +x /app/start.sh
ENV PORT=10000 SEARXNG_PORT=8080
EXPOSE 10000
ENTRYPOINT ["/bin/sh","/app/start.sh"]
