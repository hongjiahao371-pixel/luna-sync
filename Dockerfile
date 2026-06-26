FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
ENV http_proxy="" https_proxy="" HTTP_PROXY="" HTTPS_PROXY="" no_proxy="*"
RUN echo 'Acquire::http::Proxy "false";' > /etc/apt/apt.conf.d/99np && echo 'Acquire::https::Proxy "false";' >> /etc/apt/apt.conf.d/99np && sed -i 's|archive.ubuntu.com|mirrors.tuna.tsinghua.edu.cn|g; s|security.ubuntu.com|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/ubuntu.sources && apt-get update && apt-get install -y --no-install-recommends network-manager wpasupplicant iw python3 python3-flask python3-pil ffmpeg iproute2 curl ca-certificates procps && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY app/ /app/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 8765
ENTRYPOINT ["/entrypoint.sh"]
