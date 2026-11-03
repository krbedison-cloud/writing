#!/usr/bin/env bash
# Install a tagged release on Docker Desktop without Git or a Python installation.
set -euo pipefail
release="${SHENLUN_VERSION:-v0.1.11}"
install_dir="${SHENLUN_DIR:-$HOME/shenlun-studio}"
requested_port="${SHENLUN_PORT:-}"
requested_lan="${SHENLUN_LAN:-}"
if [[ -n "$requested_lan" && "$requested_lan" != 0 && "$requested_lan" != 1 ]]; then
  echo 'SHENLUN_LAN 必须是 0（仅本机）或 1（局域网访问）。' >&2
  exit 1
fi
if [[ -n "$requested_port" ]]; then
  if [[ ! "$requested_port" =~ ^[0-9]{1,5}$ ]]; then
    echo 'SHENLUN_PORT 必须是 1–65535 的端口号。' >&2
    exit 1
  fi
  requested_port="$((10#$requested_port))"
  if (( requested_port < 1 || requested_port > 65535 )); then
    echo 'SHENLUN_PORT 必须是 1–65535 的端口号。' >&2
    exit 1
  fi
fi
repo="krbedison-cloud/writing"
image="ghcr.io/$repo:$release"
if [[ ! "$release" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo 'SHENLUN_VERSION 必须是 v0.1.11 这样的版本标签。' >&2
  exit 1
fi
for dependency in docker curl tar; do
  if ! command -v "$dependency" >/dev/null 2>&1; then
    echo "缺少 $dependency；请先安装并启动 Docker Desktop。" >&2
    exit 1
  fi
done
if ! docker info >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo '请先启动 Docker Desktop，并确认 docker compose 可用。' >&2
  exit 1
fi
umask 077
mkdir -p "$install_dir"
staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT
compose_file="$install_dir/compose-$release.yaml"
echo "准备申论进阶 $release …"
curl --fail --silent --show-error --location --retry 3 \
  "https://raw.githubusercontent.com/$repo/$release/compose.deploy.yaml" -o "$staging/compose.yaml"
if [[ -f "$compose_file" ]]; then
  if ! cmp -s "$compose_file" "$staging/compose.yaml"; then
    echo "配置已存在且与发行版不同：$compose_file。为保护你的修改，本次未覆盖；请先备份或换一个 SHENLUN_DIR。" >&2
    exit 1
  fi
else
  cp "$staging/compose.yaml" "$compose_file"
fi
if [[ ! -f "$install_dir/.env" ]]; then
  cat > "$install_dir/.env" <<'ENV'
APP_PORT=18086
APP_BIND=127.0.0.1
ALLOW_LAN=0
APP_PASSWORD=
ALLOWED_HOSTS=localhost,127.0.0.1
ENV
fi
# Explicit requests update only the named settings, preserving passwords and custom lines.
set_config() {
  local key="$1" value="$2"
  awk -v key="$key" -v value="$value" '
    BEGIN { found=0 }
    $0 ~ "^[[:space:]]*(export[[:space:]]+)?" key "[[:space:]]*=" {
      if (!found) { print key "=" value; found=1 }
      next
    }
    { print }
    END { if (!found) print key "=" value }
  ' "$install_dir/.env" > "$staging/env"
  cat "$staging/env" > "$install_dir/.env"
}
if [[ -n "$requested_port" ]]; then
  set_config APP_PORT "$requested_port"
  export APP_PORT="$requested_port"
fi
if [[ -n "$requested_lan" ]]; then
  if [[ "$requested_lan" == 1 ]]; then
    set_config APP_BIND 0.0.0.0
    export APP_BIND=0.0.0.0
  else
    set_config APP_BIND 127.0.0.1
    export APP_BIND=127.0.0.1
  fi
  set_config ALLOW_LAN "$requested_lan"
  export ALLOW_LAN="$requested_lan"
fi
if docker pull "$image"; then
  echo '镜像拉取成功。'
else
  architecture="$(docker info --format '{{.Architecture}}')"
  case "$architecture" in
    arm64|aarch64) architecture=arm64 ;;
    amd64|x86_64) architecture=amd64 ;;
    *) echo "未支持的 Docker 架构：$architecture" >&2; exit 1 ;;
  esac
  echo "尝试从 GitHub Release 下载 $architecture 预构建镜像 …"
  if curl --fail --silent --show-error --location --retry 3 \
    "https://github.com/$repo/releases/download/$release/shenlun-studio-$release-linux-$architecture.tar.gz" -o "$staging/image.tar.gz"; then
    docker load -i "$staging/image.tar.gz"
  else
    echo '发行镜像尚在生成，将下载同版本源码包本地构建（不使用 git clone）。'
    curl --fail --silent --show-error --location --retry 3 \
    "https://github.com/$repo/archive/refs/tags/$release.tar.gz" -o "$staging/source.tar.gz"
  mkdir "$staging/source"
  tar -xzf "$staging/source.tar.gz" --strip-components=1 -C "$staging/source"
    docker build -t "$image" "$staging/source"
  fi
fi
SHENLUN_IMAGE="$image" docker compose --project-name shenlun-studio --env-file "$install_dir/.env" \
  -f "$compose_file" up -d --pull never --wait --wait-timeout 120
address="$(SHENLUN_IMAGE="$image" docker compose --project-name shenlun-studio --env-file "$install_dir/.env" -f "$compose_file" port shenlun 8000)"
echo
echo "安装完成，浏览器打开：http://localhost:${address##*:}"
lan_enabled="${ALLOW_LAN:-$(awk -F= '$1 == "ALLOW_LAN" {gsub(/["[:space:]]/, "", $2); print $2}' "$install_dir/.env")}"
if [[ "$lan_enabled" == 1 ]]; then
  local_ips=""
  if command -v ipconfig >/dev/null 2>&1 && command -v ifconfig >/dev/null 2>&1; then
    for interface in $(ifconfig -l); do
      local_ip="$(ipconfig getifaddr "$interface" 2>/dev/null || true)"
      local_ips="$local_ips $local_ip"
    done
  elif command -v ip >/dev/null 2>&1; then
    local_ips="$(ip -o -4 addr show scope global | awk '{split($4,a,"/"); print a[1]}')"
  fi
  found_ip=0
  for local_ip in $local_ips; do
    case "$local_ip" in
      10.*|192.168.*|169.254.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*)
        echo "局域网访问：http://$local_ip:${address##*:}"
        found_ip=1 ;;
    esac
  done
  if [[ "$found_ip" == 0 ]]; then
    echo "局域网访问：http://<Mac局域网IP>:${address##*:}（在系统设置 → 网络中查看 IP）"
  fi
fi
echo "配置保存在：$install_dir"
echo '数据保存在 Docker 数据卷 shenlun-studio-data；停止或更新容器不会删除数据。'
printf '停止命令：docker compose --project-name shenlun-studio --env-file %q -f %q down\n' "$install_dir/.env" "$compose_file"
