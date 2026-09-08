#!/usr/bin/env bash
#
# Серверная половина выката. Запускается под sudo одним заходом — потому что sudo
# спрашивает пароль на каждый tty, а выкат из десяти шагов не должен спрашивать его
# десять раз.
#
#   remote-push.sh <ROOT> <SERVICE_USER> <UNIT> <STAMP> <STAGING> <KEEP>
#
# Порядок здесь и есть вся защита. Код сначала оказывается на месте, потом
# проверяется НЕ переключённым — импортом в том самом venv, которым его будут
# запускать, — и только потом symlink переезжает. Сервис перезапускается последним
# и, если не поднялся, symlink возвращается назад в этом же заходе: откат случается
# сам, пока ты ещё смотришь в терминал, а не завтра.
set -euo pipefail

ROOT="$1"; SVC="$2"; UNIT="$3"; STAMP="$4"; STAGING="$5"; KEEP="${6:-5}"
NEW="$ROOT/.releases/$STAMP"

[ -d "$STAGING/$STAMP" ] || { echo "!! в $STAGING/$STAMP пусто — rsync не доехал"; exit 1; }

# --- релиз на месте ------------------------------------------------------

rm -rf "$NEW"
mkdir -p "$ROOT/.releases"
mv "$STAGING/$STAMP" "$NEW"
chown -R "$SVC:$SVC" "$NEW"
chmod -R a-w "$NEW"          # релиз, на который может понадобиться откатиться,
chmod u+w "$NEW"             # не должен быть тем, что правит работающий сервис

# --- зависимости ---------------------------------------------------------
#
# Один venv на все релизы: пакет не ставится вовсе (editable install прибил бы venv
# к одному каталогу и сломал бы откат), код находится через PYTHONPATH. Поэтому
# переустанавливать надо только когда список зависимостей действительно изменился —
# сравниваем хеш файла, а не его дату.
WANT="$(sha256sum "$NEW/requirements.txt" | cut -d' ' -f1)"
HAVE="$(cat "$ROOT/.venv/.requirements.sha" 2>/dev/null || true)"
if [ "$WANT" != "$HAVE" ]; then
  echo ">> зависимости изменились, ставлю"
  sudo -u "$SVC" "$ROOT/.venv/bin/pip" install -q --upgrade pip
  sudo -u "$SVC" "$ROOT/.venv/bin/pip" install -q -r "$NEW/requirements.txt"
  echo "$WANT" | sudo -u "$SVC" tee "$ROOT/.venv/.requirements.sha" >/dev/null
else
  echo ">> зависимости те же"
fi

# --- проверка до переключения -------------------------------------------
#
# Импорт — это не тест, но он ловит ровно тот класс поломки, который иначе
# обнаруживается перезапущенным и не поднявшимся сервисом: опечатка, забытый файл,
# зависимость, которой нет в requirements.
echo ">> проверяю релиз, не переключая"
if ! sudo -u "$SVC" env PYTHONPATH="$NEW/src" "$ROOT/.venv/bin/python" \
     -c 'import slopgen.cli.app, slopgen.bot.service, slopgen.web.app' 2>&1; then
  echo "!! новый релиз не импортируется — ничего не переключаю, живой остался жить"
  exit 1
fi

# --- юнит ----------------------------------------------------------------

if [ -f "$NEW/deploy/$UNIT.service" ] && \
   ! cmp -s "$NEW/deploy/$UNIT.service" "/etc/systemd/system/$UNIT.service"; then
  echo ">> юнит изменился, ставлю"
  cp "$NEW/deploy/$UNIT.service" "/etc/systemd/system/$UNIT.service"
  systemctl daemon-reload
  systemctl enable "$UNIT" >/dev/null 2>&1 || true
fi

# --- переключение --------------------------------------------------------

PREV="$(readlink -f "$ROOT/current" 2>/dev/null || true)"
ln -sfn "$NEW" "$ROOT/current.tmp"
mv -T "$ROOT/current.tmp" "$ROOT/current"   # атомарно: current всегда куда-то ведёт
chown -h "$SVC:$SVC" "$ROOT/current"
echo ">> current -> $STAMP"

systemctl restart "$UNIT"
sleep 5
if ! systemctl is-active --quiet "$UNIT"; then
  echo "!! сервис не поднялся. Откатываюсь."
  journalctl -u "$UNIT" -n 20 --no-pager | sed 's/^/   /'
  if [ -n "$PREV" ] && [ -d "$PREV" ]; then
    ln -sfn "$PREV" "$ROOT/current.tmp"; mv -T "$ROOT/current.tmp" "$ROOT/current"
    systemctl restart "$UNIT"; sleep 3
    echo ">> вернулся на $(basename "$PREV"): $(systemctl is-active "$UNIT")"
  fi
  exit 1
fi

# --- уборка --------------------------------------------------------------
#
# Старые релизы — это и есть возможность откатиться, поэтому их несколько, а не
# один. Текущий никогда не попадает под нож, даже если он старше остальных.
CURRENT="$(basename "$(readlink -f "$ROOT/current")")"
cd "$ROOT/.releases"
ls -1 | sort -r | tail -n "+$((KEEP + 1))" | while read -r old; do
  [ "$old" = "$CURRENT" ] && continue
  chmod -R u+w "$old" && rm -rf "$old" && echo ">> убрал старый релиз $old"
done
rm -rf "${STAGING:?}/${STAMP:?}" 2>/dev/null || true

echo ">> выкат $STAMP: $(systemctl is-active "$UNIT")"
