#!/usr/bin/env bash
#
# Выкат slopgen на сервер с systemd.
#
#   ./deploy.sh bootstrap        чистая машина: пакеты, юзер, каталоги, venv,
#                                cloudflared, юнит — и первый выкат
#   ./deploy.sh push             собрать релиз, переключить, перезапустить
#   ./deploy.sh rollback         вернуть предыдущий релиз
#   ./deploy.sh seed [что...]    залить ЛОКАЛЬНЫЕ configs/ .env assets/ ПОВЕРХ
#                                серверных (затирает живое — только руками)
#   ./deploy.sh pull [куда]      забрать готовые ролики с сервера
#
#   ./deploy.sh status           что стоит, что запущено, где панель
#   ./deploy.sh url              адрес панели прямо сейчас
#   ./deploy.sh logs [-n 200]    journalctl -f по юниту
#   ./deploy.sh restart | stop | start
#
#   ./deploy.sh allow <id>       вписать Telegram-id в список допущенных
#   ./deploy.sh setenv KEY VAL   поправить ключ в серверном .env
#   ./deploy.sh run -- <args>    выполнить `slopgen <args>` на сервере
#   ./deploy.sh ssh              просто зайти в каталог состояния
#
# Настройка: cp deploy.env.example deploy.env, заполнить deploy.env.
#
# Как это устроено и почему так.
#
# Выкат НЕ копирует файлы поверх работающего кода. Он собирает релиз рядом,
# проверяет, что тот хотя бы импортируется, и только потом переключает симлинк
# current. Поэтому откат — это `ln -sfn` на прошлый каталог, а не «восстановить из
# бэкапа, которого нет».
#
# Состояние живёт отдельно от кода и НИКОГДА не ездит выкатом: configs/, .env,
# assets/, output/, models/ лежат в .state и остаются серверными. Это ровно тот
# случай, когда «мой ноутбук — источник истины» ломает: список допущенных, токен
# бота и папки с готовыми роликами правились ТАМ.
#
# venv один на все релизы. Пакет не ставится (`pip install -e` прибил бы venv к
# одному релизу и сломал бы откат) — код находится через PYTHONPATH на current.
# Зависимости переустанавливаются только когда requirements.txt действительно
# изменился: сравнивается хеш, а не дата.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/deploy.env"
[ -f "$ENV_FILE" ] || { echo "нет $ENV_FILE — скопируй deploy.env.example в deploy.env и заполни"; exit 1; }
# shellcheck disable=SC1090
source "$ENV_FILE"

: "${SSH_HOST:?задай SSH_HOST в deploy.env}"
: "${SSH_USER:?задай SSH_USER в deploy.env}"
SERVICE_USER="${SERVICE_USER:-slopgen}"
REMOTE_ROOT="${REMOTE_ROOT:-/opt/slopgen}"
UNIT="${UNIT:-slopgen-bot}"
SEED="${SEED:-configs .env assets}"
WANT_CLOUDFLARED="${WANT_CLOUDFLARED:-1}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"
SSH_TARGET="${SSH_USER}@${SSH_HOST}"
STAGING="/home/${SSH_USER}/.slopgen-staging"

# Одно ssh-соединение на весь выкат. sudo кэширует ввод пароля по tty, а каждый
# заход получает свой — без этого пароль спрашивался бы на каждый шаг.
SSH_CONTROL="/tmp/.slopdeploy-$$-$(printf '%s' "$SSH_TARGET" | tr -c 'a-zA-Z0-9' '_')"
SSH_OPTS=(-o ControlMaster=auto -o "ControlPath=$SSH_CONTROL" -o ControlPersist=120)

close_connection() { ssh -O exit "${SSH_OPTS[@]}" "$SSH_TARGET" 2>/dev/null || true; }
trap close_connection EXIT

open_connection() {
  ssh -O check "${SSH_OPTS[@]}" "$SSH_TARGET" 2>/dev/null && return
  echo ">> соединяюсь с $SSH_TARGET"
  ssh -fN "${SSH_OPTS[@]}" "$SSH_TARGET"
}

remote()  { ssh -t "${SSH_OPTS[@]}" "$SSH_TARGET" "$@"; }   # с tty — для sudo
quiet()   { ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "$@"; }      # без tty — для вывода

# Выполнить кусок bash на сервере под sudo. Скрипт СНАЧАЛА уезжает туда файлом и
# только потом запускается — а не подаётся в stdin вместе с `-t`: sudo спрашивает
# пароль на том же псевдотерминале, из которого `bash -s` читал бы свой текст, и они
# дерутся за один поток. Симптом — выкат, который молча висит.
#
#   remote_sudo <имя> <аргументы...> <<'SH' ... SH
remote_sudo() {
  local name="$1"; shift
  local path="/tmp/slopgen-deploy-$name.sh"
  quiet "cat > $path"                       # stdin здесь — heredoc вызывающего
  local args="" a
  for a in "$@"; do args="$args $(printf '%q' "$a")"; done
  remote "sudo bash $path$args; rc=\$?; rm -f $path; exit \$rc"
}

stamp()   { date +%Y%m%d-%H%M%S; }
note()    { git -C "$SCRIPT_DIR" log -1 --pretty='%h %s' 2>/dev/null || date '+сборка %d.%m %H:%M'; }

# --- код -----------------------------------------------------------------

# Что вообще едет. Всё остальное — состояние или мусор, и на сервере ему нечего
# делать: configs/ и assets/ там СВОИ (см. seed), output/ и models/ весят гигабайты,
# а .env — это ключи, которые правятся на месте.
CODE_EXCLUDES=(
  --exclude '.git/' --exclude '.venv/' --exclude '__pycache__/' --exclude '*.pyc'
  --exclude '*.egg-info/' --exclude 'output/' --exclude 'state/' --exclude 'models/'
  --exclude 'configs/' --exclude 'assets/' --exclude '.env' --exclude 'deploy.env'
  --exclude '.bundles/' --exclude 'secrets/'
)

cmd_push() {
  open_connection
  local st; st="$(stamp)"
  echo ">> собираю релиз $st ($(note))"
  quiet "mkdir -p $STAGING/$st"
  rsync -az --delete -e "ssh ${SSH_OPTS[*]}" "${CODE_EXCLUDES[@]}" \
        "$SCRIPT_DIR/" "$SSH_TARGET:$STAGING/$st/"
  echo ">> переключаю (пароль sudo — один раз на весь выкат)"
  remote_sudo push "$REMOTE_ROOT" "$SERVICE_USER" "$UNIT" "$st" "$STAGING" "$KEEP_RELEASES" \
    < "$SCRIPT_DIR/deploy/remote-push.sh"
}

cmd_rollback() {
  open_connection
  remote_sudo rollback "$REMOTE_ROOT" "$UNIT" <<'SH'
set -euo pipefail
ROOT="$1"; UNIT="$2"
cd "$ROOT/.releases"
now="$(basename "$(readlink -f "$ROOT/current")")"
prev="$(ls -1 | sort | awk -v n="$now" '$0 < n' | tail -1)"
[ -n "$prev" ] || { echo "!! откатываться некуда: релиз $now единственный"; exit 1; }
ln -sfn "$ROOT/.releases/$prev" "$ROOT/current.tmp"
mv -T "$ROOT/current.tmp" "$ROOT/current"
systemctl restart "$UNIT"
sleep 3
systemctl is-active "$UNIT" && echo ">> откатились на $prev"
SH
}

# --- первичная установка -------------------------------------------------

cmd_bootstrap() {
  open_connection
  echo ">> ставлю пакеты и завожу $SERVICE_USER"
  remote_sudo bootstrap "$REMOTE_ROOT" "$SERVICE_USER" "$SSH_USER" "$WANT_CLOUDFLARED" <<'SH'
set -euo pipefail
ROOT="$1"; SVC="$2"; ME="$3"; WANT_CF="$4"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# ffmpeg — это и есть монтажка; без него ставится всё и не рендерится ничего.
apt-get install -y -qq python3-venv python3-pip ffmpeg rsync curl fonts-dejavu-core
id -u "$SVC" >/dev/null 2>&1 || adduser --system --group --home "$ROOT" "$SVC"
usermod -aG "$SVC" "$ME"
mkdir -p "$ROOT/.releases" "$ROOT/.state/configs" "$ROOT/.state/assets" \
         "$ROOT/.state/output" "$ROOT/.state/state" "$ROOT/.state/models"
[ -d "$ROOT/.venv" ] || python3 -m venv "$ROOT/.venv"
chown -R "$SVC:$SVC" "$ROOT"
chmod 755 "$ROOT"
if [ "$WANT_CF" = "1" ] && ! command -v cloudflared >/dev/null; then
  # Публичный https-адрес для мини-приложения. Без него чат работает, а кнопка
  # «панель» — нет: Telegram открывает мини-приложения только по https.
  arch="$(dpkg --print-architecture)"
  echo ">> ставлю cloudflared ($arch)"
  curl -fsSL -o /tmp/cloudflared.deb \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${arch}.deb"
  dpkg -i /tmp/cloudflared.deb && rm -f /tmp/cloudflared.deb
fi
echo ">> готово: $ROOT"
SH
  echo ">> заливаю локальное состояние (configs/, .env, assets/)"
  cmd_seed
  cmd_push
  cat <<EOF

>> дальше — руками, по одному разу:
     ./deploy.sh setenv TELEGRAM_BOT_TOKEN <токен от @BotFather>
     ./deploy.sh allow <твой telegram id>      # бот скажет его сам, если написать ему
     ./deploy.sh restart
     ./deploy.sh url                            # адрес панели
EOF
}

# --- состояние -----------------------------------------------------------

# Заливает ЛОКАЛЬНОЕ состояние поверх серверного. Отдельной командой и никогда из
# push: настройки на сервере живут своей жизнью — там правится список допущенных,
# там лежит токен, и затирать это выкатом было бы способом однажды выключить бота.
cmd_seed() {
  open_connection
  local what=("$@"); [ ${#what[@]} -gt 0 ] || read -r -a what <<<"$SEED"
  quiet "mkdir -p $STAGING/state"
  for item in "${what[@]}"; do
    [ -e "$SCRIPT_DIR/$item" ] || { echo "   пропускаю $item — нет такого"; continue; }
    echo ">> заливаю $item"
    if [ -d "$SCRIPT_DIR/$item" ]; then
      rsync -az -e "ssh ${SSH_OPTS[*]}" --exclude '__pycache__/' \
            "$SCRIPT_DIR/$item/" "$SSH_TARGET:$STAGING/state/$item/"
    else
      rsync -az -e "ssh ${SSH_OPTS[*]}" "$SCRIPT_DIR/$item" "$SSH_TARGET:$STAGING/state/"
    fi
  done
  remote "sudo bash -c 'cp -a $STAGING/state/. $REMOTE_ROOT/.state/ && \
                        chown -R $SERVICE_USER:$SERVICE_USER $REMOTE_ROOT/.state && \
                        rm -rf $STAGING/state'"
  echo ">> состояние на месте: $REMOTE_ROOT/.state"
}

cmd_pull() {
  open_connection
  local dest="${1:-$SCRIPT_DIR/output}"
  mkdir -p "$dest"
  echo ">> забираю ролики в $dest"
  rsync -az --info=progress2 -e "ssh ${SSH_OPTS[*]}" \
        --include '*/' --include '*.mp4' --include '*.json' --include '*.txt' --exclude '*' \
        "$SSH_TARGET:$REMOTE_ROOT/.state/output/" "$dest/"
}

cmd_allow() {
  local who="${1:?укажи Telegram-id}"
  open_connection
  remote "sudo bash -c 'f=$REMOTE_ROOT/.state/configs/bot_allow.txt; touch \$f; \
          grep -qx \"$who\" \$f || echo \"$who\" >> \$f; \
          chown $SERVICE_USER:$SERVICE_USER \$f; cat \$f'"
  echo ">> список перечитывается сам, перезапуск не нужен"
}

cmd_setenv() {
  local key="${1:?имя переменной}" value="${2:?значение}"
  open_connection
  remote_sudo setenv "$REMOTE_ROOT/.state/.env" "$SERVICE_USER" "$key" "$value" <<'SH'
set -euo pipefail
F="$1"; SVC="$2"; K="$3"; V="$4"
touch "$F"
if grep -q "^$K=" "$F"; then
  sed -i "s|^$K=.*|$K=$V|" "$F"
else
  printf '%s=%s\n' "$K" "$V" >> "$F"
fi
chown "$SVC:$SVC" "$F"; chmod 600 "$F"
echo ">> $K записан в $F"
SH
  echo ">> перезапусти, чтобы подхватил: ./deploy.sh restart"
}

# --- наблюдение ----------------------------------------------------------

cmd_status() {
  open_connection
  quiet "bash -s -- '$REMOTE_ROOT' '$UNIT'" <<'SH'
set -uo pipefail
ROOT="$1"; UNIT="$2"
echo "релиз:   $(basename "$(readlink -f "$ROOT/current" 2>/dev/null)" 2>/dev/null || echo '—')"
echo "сервис:  $(systemctl is-active "$UNIT" 2>/dev/null) / $(systemctl is-enabled "$UNIT" 2>/dev/null)"
echo "панель:  $(cat "$ROOT/.state/state/bot.url" 2>/dev/null || echo 'адреса нет')"
echo "допуск:  $(grep -cvE '^\s*(#|$)' "$ROOT/.state/configs/bot_allow.txt" 2>/dev/null || echo 0) id"
echo "ролики:  $(find "$ROOT/.state/output" -name '*.mp4' 2>/dev/null | wc -l) шт, $(du -sh "$ROOT/.state/output" 2>/dev/null | cut -f1)"
echo "релизы:  $(ls -1 "$ROOT/.releases" 2>/dev/null | tr '\n' ' ')"
echo
systemctl status "$UNIT" --no-pager -n 5 2>/dev/null | tail -8
SH
}

cmd_url()     { open_connection; quiet "cat $REMOTE_ROOT/.state/state/bot.url 2>/dev/null || echo 'адреса нет — посмотри ./deploy.sh logs'"; }
cmd_logs()    { ssh -t "${SSH_OPTS[@]}" "$SSH_TARGET" "journalctl -u $UNIT ${*:-'-n' '100'} -f"; }
cmd_restart() { open_connection; remote "sudo systemctl restart $UNIT && sleep 2 && systemctl is-active $UNIT"; }
cmd_start()   { open_connection; remote "sudo systemctl start $UNIT && systemctl is-active $UNIT"; }
cmd_stop()    { open_connection; remote "sudo systemctl stop $UNIT; systemctl is-active $UNIT || true"; }

# Слопген на сервере — тем же питоном, теми же конфигами, из того же каталога, что
# и сервис. Годится для `--list-types`, `models install`, разовой генерации.
cmd_run() {
  open_connection
  remote "sudo -u $SERVICE_USER env PYTHONPATH=$REMOTE_ROOT/current/src \
          bash -lc 'cd $REMOTE_ROOT/.state && $REMOTE_ROOT/.venv/bin/python -m slopgen $*'"
}

cmd_ssh() { ssh -t "${SSH_OPTS[@]}" "$SSH_TARGET" "cd $REMOTE_ROOT/.state && exec \$SHELL -l"; }

case "${1:-}" in
  push)       shift; cmd_push "$@" ;;
  rollback)   shift; cmd_rollback ;;
  bootstrap)  shift; cmd_bootstrap ;;
  seed)       shift; cmd_seed "$@" ;;
  pull)       shift; cmd_pull "$@" ;;
  allow)      shift; cmd_allow "$@" ;;
  setenv)     shift; cmd_setenv "$@" ;;
  status)     cmd_status ;;
  url)        cmd_url ;;
  logs)       shift; cmd_logs "$@" ;;
  restart)    cmd_restart ;;
  start)      cmd_start ;;
  stop)       cmd_stop ;;
  run)        shift; [ "${1:-}" = "--" ] && shift; cmd_run "$@" ;;
  ssh)        cmd_ssh ;;
  *) sed -n '3,30p' "$0" | sed 's/^# \?//'; exit 1 ;;
esac
