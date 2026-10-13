#!/bin/sh
# Hashcat 6.2.6 dereferences getpwuid_r's result even for an unmapped UID.
# Provide this process with a private NSS entry; never modify /etc/passwd.
set -eu
umask 077
nss_dir=$(mktemp -d /work/.hashcat-nss.XXXXXX)
trap 'rm -f "$nss_dir/passwd" "$nss_dir/group"; rmdir "$nss_dir"' EXIT
printf 'ctfbot:x:%s:%s:ctfbot:/work:/bin/sh\n' "$(id -u)" "$(id -g)" > "$nss_dir/passwd"
printf 'ctfbot:x:%s:\n' "$(id -g)" > "$nss_dir/group"
export NSS_WRAPPER_PASSWD="$nss_dir/passwd" NSS_WRAPPER_GROUP="$nss_dir/group"
export LD_PRELOAD="/usr/lib/x86_64-linux-gnu/libnss_wrapper.so${LD_PRELOAD:+:$LD_PRELOAD}"
export XDG_DATA_HOME="${XDG_DATA_HOME:-/work/.local/share}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-/work/.cache}"
export POCL_MAX_PTHREAD_COUNT="${POCL_MAX_PTHREAD_COUNT:-2}"
status=0
/usr/bin/hashcat "$@" || status=$?
exit "$status"
