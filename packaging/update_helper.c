/*
 * flackey-update-helper -- swap a staged Flackey.app in for the installed one, after the app quits.
 *
 * Compiled C rather than a shell script for RENAME_SWAP: two `mv`s leave a window in which
 * /Applications/Flackey.app does not exist. Everything above the swap is input validation, and it
 * is the security boundary -- this runs detached and unwatched against a path in /Applications, so
 * every check and the swap are relative to one directory descriptor opened once, never by path.
 *
 * Nothing here runs as root. See docs/specs/2026-09-19-issue-58-update-design.md.
 */

#include <CommonCrypto/CommonDigest.h>
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <removefile.h>
#include <signal.h>
#include <spawn.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/xattr.h>
#include <time.h>
#include <unistd.h>

#define TARGET_NAME "Flackey.app"
#define BUNDLE_ID "com.flackey.app"
#define STAGING_PREFIX ".Flackey-staging-"
#define STAGING_SUFFIX ".app"
#define STAGING_MIN_RANDOM 6
/* Long enough for a quit that has to stop a sidecar and flush a database, short enough that a hung app
 * does not leave this program resident for the rest of the session. */
#define WAIT_TIMEOUT_MS 60000
#define WAIT_POLL_MS 50

static FILE *logfile = NULL;

static void logline(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vfprintf(logfile ? logfile : stderr, fmt, ap);
    va_end(ap);
    fputc('\n', logfile ? logfile : stderr);
    if (logfile) fflush(logfile);
}

/* Read only by a human in the log: "refused" vs "tried and failed". */
enum {
    EXIT_OK = 0,
    EXIT_USAGE = 2,
    EXIT_REFUSED = 3,      /* a validation rule said no; nothing was touched */
    EXIT_SWAP_BLOCKED = 4, /* the kernel refused the swap -- App Management, most likely */
    EXIT_SWAP_FAILED = 5,
    EXIT_PARENT_ALIVE = 6,
};

/* The only thing tying the directory about to be swapped in to one this app created -- and it
 * arrives as an argument, which is what an attacker controls. */
static int staging_name_ok(const char *name) {
    size_t len = strlen(name);
    size_t prefix = strlen(STAGING_PREFIX), suffix = strlen(STAGING_SUFFIX);
    if (len < prefix + STAGING_MIN_RANDOM + suffix) return 0;
    if (strncmp(name, STAGING_PREFIX, prefix) != 0) return 0;
    if (strcmp(name + len - suffix, STAGING_SUFFIX) != 0) return 0;
    for (size_t i = prefix; i < len - suffix; i++) {
        if (!isalnum((unsigned char)name[i])) return 0;
    }
    return 1;
}

/* Never `stat`: following a symlink here is how an updater is talked into writing elsewhere. */
static int is_real_directory(int dirfd, const char *name, struct stat *out) {
    if (fstatat(dirfd, name, out, AT_SYMLINK_NOFOLLOW) != 0) {
        logline("refusing: cannot stat %s: %s", name, strerror(errno));
        return 0;
    }
    if (S_ISLNK(out->st_mode)) {
        logline("refusing: %s is a symlink", name);
        return 0;
    }
    if (!S_ISDIR(out->st_mode)) {
        logline("refusing: %s is not a directory", name);
        return 0;
    }
    return 1;
}

static int has_quarantine(int fd) {
    ssize_t n = fgetxattr(fd, "com.apple.quarantine", NULL, 0, 0, 0);
    return n >= 0;
}

/* A search, not a parse: Info.plist may be binary, and linking CoreFoundation to read two strings
 * is a bad trade. A sanity check only -- the security work is done by the rules above. */
static int declares_flackey_bundle_id(int stagefd) {
    int fd = openat(stagefd, "Contents/Info.plist", O_RDONLY | O_NOFOLLOW);
    if (fd < 0) {
        logline("refusing: no readable Contents/Info.plist: %s", strerror(errno));
        return 0;
    }
    char buf[65536];
    ssize_t n = read(fd, buf, sizeof(buf) - 1);
    close(fd);
    if (n <= 0) {
        logline("refusing: Contents/Info.plist is empty or unreadable");
        return 0;
    }
    buf[n] = '\0';
    /* memmem rather than strstr: a binary plist is full of NULs and strstr would stop at the first. */
    int has_key = memmem(buf, (size_t)n, "CFBundleIdentifier", 18) != NULL;
    int has_id = memmem(buf, (size_t)n, BUNDLE_ID, strlen(BUNDLE_ID)) != NULL;
    if (!has_key || !has_id) {
        logline("refusing: Info.plist does not declare CFBundleIdentifier " BUNDLE_ID);
        return 0;
    }
    return 1;
}

/* A bundle with no executable satisfies every other rule and still cannot be opened. */
static int has_runnable_executable(int stagefd) {
    struct stat st;
    if (fstatat(stagefd, "Contents/MacOS/Flackey", &st, AT_SYMLINK_NOFOLLOW) != 0) {
        logline("refusing: staged bundle has no Contents/MacOS/Flackey: %s", strerror(errno));
        return 0;
    }
    if (!S_ISREG(st.st_mode) || !(st.st_mode & S_IXUSR)) {
        logline("refusing: staged bundle's executable is not an executable file");
        return 0;
    }
    return 1;
}

/* The manifest, as src/flackey/selfupdate/install.py `manifest` computes it, byte for byte: a sha256 over
 * every entry below the bundle, directory by directory (pre-order, each directory's own entries first,
 * all names sorted together), feeding for each entry its relative path, NUL, its st_mode in decimal, NUL,
 * then a symlink's target or the sha256 of a file's contents, then NUL. Symlinks are never followed.
 * Python sorts names by code point, and strcmp over UTF-8 gives the same order. */
#define DIGEST_HEX_LEN (CC_SHA256_DIGEST_LENGTH * 2)

static int parse_digest(const char *hex, unsigned char out[CC_SHA256_DIGEST_LENGTH]) {
    if (strlen(hex) != DIGEST_HEX_LEN) return 0;
    for (size_t i = 0; i < CC_SHA256_DIGEST_LENGTH; i++) {
        unsigned int byte;
        if (!isxdigit((unsigned char)hex[2 * i]) || !isxdigit((unsigned char)hex[2 * i + 1])) return 0;
        if (sscanf(hex + 2 * i, "%2x", &byte) != 1) return 0;
        out[i] = (unsigned char)byte;
    }
    return 1;
}

static int by_name(const void *a, const void *b) {
    return strcmp(*(char *const *)a, *(char *const *)b);
}

static int hash_file(int dirfd, const char *name, unsigned char out[CC_SHA256_DIGEST_LENGTH]) {
    int fd = openat(dirfd, name, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return 0;
    CC_SHA256_CTX ctx;
    CC_SHA256_Init(&ctx);
    char buf[65536];
    ssize_t n;
    while ((n = read(fd, buf, sizeof(buf))) > 0) CC_SHA256_Update(&ctx, buf, (CC_LONG)n);
    close(fd);
    if (n < 0) return 0;
    CC_SHA256_Final(out, &ctx);
    return 1;
}

/* Takes ownership of `dirfd`. `rel` is this directory's path below the bundle, "" at the top. */
static int hash_tree(int dirfd, const char *rel, CC_SHA256_CTX *ctx) {
    DIR *dir = fdopendir(dirfd);
    if (dir == NULL) {
        close(dirfd);
        return 0;
    }
    char **names = NULL;
    size_t count = 0, cap = 0;
    int ok = 1;
    struct dirent *ent;
    errno = 0;
    while ((ent = readdir(dir)) != NULL) {
        if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0) continue;
        if (count == cap) {
            cap = cap ? cap * 2 : 64;
            char **grown = realloc(names, cap * sizeof(*names));
            if (grown == NULL) { ok = 0; break; }
            names = grown;
        }
        if ((names[count] = strdup(ent->d_name)) == NULL) { ok = 0; break; }
        count++;
    }
    if (ok && errno != 0) ok = 0;
    if (ok) qsort(names, count, sizeof(*names), by_name);

    unsigned char *is_dir = ok ? calloc(count ? count : 1, 1) : NULL;
    if (is_dir == NULL) ok = 0;
    char path[PATH_MAX];
    for (size_t i = 0; ok && i < count; i++) {
        int len = rel[0] ? snprintf(path, sizeof(path), "%s/%s", rel, names[i])
                         : snprintf(path, sizeof(path), "%s", names[i]);
        if (len < 0 || (size_t)len >= sizeof(path)) { ok = 0; break; }
        struct stat st;
        if (fstatat(dirfd, names[i], &st, AT_SYMLINK_NOFOLLOW) != 0) { ok = 0; break; }
        char mode[16];
        int mlen = snprintf(mode, sizeof(mode), "%u", (unsigned)st.st_mode);
        CC_SHA256_Update(ctx, path, (CC_LONG)len + 1); /* with its NUL */
        CC_SHA256_Update(ctx, mode, (CC_LONG)mlen + 1);
        if (S_ISLNK(st.st_mode)) {
            char target[PATH_MAX];
            ssize_t tlen = readlinkat(dirfd, names[i], target, sizeof(target));
            if (tlen < 0 || (size_t)tlen >= sizeof(target)) { ok = 0; break; }
            CC_SHA256_Update(ctx, target, (CC_LONG)tlen);
        } else if (S_ISREG(st.st_mode)) {
            unsigned char digest[CC_SHA256_DIGEST_LENGTH];
            if (!hash_file(dirfd, names[i], digest)) { ok = 0; break; }
            CC_SHA256_Update(ctx, digest, sizeof(digest));
        } else if (S_ISDIR(st.st_mode)) {
            is_dir[i] = 1;
        }
        CC_SHA256_Update(ctx, "", 1);
    }
    for (size_t i = 0; ok && i < count; i++) {
        if (!is_dir[i]) continue;
        int len = rel[0] ? snprintf(path, sizeof(path), "%s/%s", rel, names[i])
                         : snprintf(path, sizeof(path), "%s", names[i]);
        if (len < 0 || (size_t)len >= sizeof(path)) { ok = 0; break; }
        int sub = openat(dirfd, names[i], O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        if (sub < 0 || !hash_tree(sub, path, ctx)) ok = 0;
    }
    for (size_t i = 0; i < count; i++) free(names[i]);
    free(names);
    free(is_dir);
    closedir(dir);
    return ok;
}

/* 1 when the bundle open at `stagefd` still hashes to `expected`. Does not consume `stagefd`. */
static int manifest_matches(int stagefd, const unsigned char expected[CC_SHA256_DIGEST_LENGTH]) {
    int fd = dup(stagefd);
    if (fd < 0) return 0;
    CC_SHA256_CTX ctx;
    CC_SHA256_Init(&ctx);
    if (!hash_tree(fd, "", &ctx)) {
        logline("refusing: could not read the staged bundle to check it: %s", strerror(errno));
        return 0;
    }
    unsigned char got[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256_Final(got, &ctx);
    if (memcmp(got, expected, sizeof(got)) != 0) {
        logline("refusing: the staged bundle changed after Flackey checked it");
        return 0;
    }
    return 1;
}

/* 1 once the pid is gone, 0 on timeout, measured by the clock because naps overshoot on a busy
 * machine. Refuse on timeout rather than proceed: a relaunch beside a live copy is two Flackeys
 * sharing one database, which is worse than no update. */
static int wait_for_exit(pid_t pid) {
    struct timespec nap = {.tv_sec = 0, .tv_nsec = WAIT_POLL_MS * 1000000L};
    struct timespec start, now;
    clock_gettime(CLOCK_MONOTONIC, &start);
    for (;;) {
        if (kill(pid, 0) != 0 && errno == ESRCH) return 1;
        clock_gettime(CLOCK_MONOTONIC, &now);
        long elapsed_ms = (now.tv_sec - start.tv_sec) * 1000L + (now.tv_nsec - start.tv_nsec) / 1000000L;
        if (elapsed_ms >= WAIT_TIMEOUT_MS) return 0;
        nanosleep(&nap, NULL);
    }
}

static void relaunch(const char *dir) {
    char path[PATH_MAX];
    snprintf(path, sizeof(path), "%s/%s", dir, TARGET_NAME);
    char *argv[] = {"/usr/bin/open", path, NULL};
    pid_t pid;
    extern char **environ;
    if (posix_spawn(&pid, "/usr/bin/open", NULL, NULL, argv, environ) != 0) {
        logline("the swap succeeded but the relaunch did not: %s", strerror(errno));
    }
}

int main(int argc, char **argv) {
    if (argc < 6 || argc > 7) {
        fprintf(stderr, "usage: %s <parent-pid> <directory> <staging-name> <relaunch 0|1> <manifest-sha256> "
                "[logfile]\n", argv[0]);
        return EXIT_USAGE;
    }
    const char *dir = argv[2];
    const char *staging = argv[3];
    int want_relaunch = strcmp(argv[4], "1") == 0;
    /* What the app hashed just before it quit. Checked again right before the swap, so a bundle changed
     * in between is never swapped in. */
    unsigned char expected[CC_SHA256_DIGEST_LENGTH];
    if (!parse_digest(argv[5], expected)) {
        fprintf(stderr, "refusing: %s is not a sha256 digest\n", argv[5]);
        return EXIT_USAGE;
    }
    if (argc == 7) {
        /* O_NOFOLLOW: a symlink planted at the log path must not make this append to another file.
         * Anything that fails here just leaves the log on stderr. */
        int logfd = open(argv[6], O_WRONLY | O_APPEND | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600);
        if (logfd >= 0) {
            logfile = fdopen(logfd, "a");
            if (logfile == NULL) close(logfd);
        }
    }

    char *end = NULL;
    long parent = strtol(argv[1], &end, 10);
    if (end == argv[1] || *end != '\0' || parent <= 1) {
        logline("refusing: %s is not a parent pid", argv[1]);
        return EXIT_USAGE;
    }
    if (!staging_name_ok(staging)) {
        logline("refusing: %s is not a staging name this app produces", staging);
        return EXIT_REFUSED;
    }

    /* Opened once: after this the kernel never resolves `dir` again. */
    int dirfd = open(dir, O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
    if (dirfd < 0) {
        logline("refusing: cannot open %s as a directory: %s", dir, strerror(errno));
        return EXIT_REFUSED;
    }

    if (!wait_for_exit((pid_t)parent)) {
        logline("refusing: pid %ld was still running after %d ms", parent, WAIT_TIMEOUT_MS);
        return EXIT_PARENT_ALIVE;
    }

    struct stat staged_st, target_st;
    if (!is_real_directory(dirfd, staging, &staged_st)) return EXIT_REFUSED;
    if (!is_real_directory(dirfd, TARGET_NAME, &target_st)) return EXIT_REFUSED;

    /* Against the real uid: this is never setuid and must not become useful to anyone who makes
     * it so. */
    if (staged_st.st_uid != getuid()) {
        logline("refusing: %s is owned by uid %u, not %u", staging, staged_st.st_uid, getuid());
        return EXIT_REFUSED;
    }

    int stagefd = openat(dirfd, staging, O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
    if (stagefd < 0) {
        logline("refusing: cannot open %s: %s", staging, strerror(errno));
        return EXIT_REFUSED;
    }
    /* The directory just opened must be the one checked above, not one renamed into its place since. */
    struct stat opened_st;
    if (fstat(stagefd, &opened_st) != 0 || opened_st.st_dev != staged_st.st_dev ||
        opened_st.st_ino != staged_st.st_ino) {
        logline("refusing: %s changed while it was being checked", staging);
        close(stagefd);
        return EXIT_REFUSED;
    }
    /* The app strips this after unpacking; still being here means an assumption broke upstream, and
     * Gatekeeper refuses a quarantined ad-hoc bundle outright. */
    if (has_quarantine(stagefd)) {
        logline("refusing: %s still carries com.apple.quarantine", staging);
        close(stagefd);
        return EXIT_REFUSED;
    }
    if (!declares_flackey_bundle_id(stagefd) || !has_runnable_executable(stagefd) ||
        !manifest_matches(stagefd, expected)) {
        close(stagefd);
        return EXIT_REFUSED;
    }
    close(stagefd);

    /* Compiled-in, not an argument: exactly one path this program may overwrite. */
    if (renameatx_np(dirfd, staging, dirfd, TARGET_NAME, RENAME_SWAP) != 0) {
        int err = errno;
        if (err == EPERM || err == EACCES) {
            logline("macOS blocked the update: grant Flackey permission under System Settings -> "
                    "Privacy & Security -> App Management (%s)", strerror(err));
            close(dirfd);
            return EXIT_SWAP_BLOCKED;
        }
        logline("the swap failed, both bundles left as they were: %s", strerror(err));
        close(dirfd);
        return EXIT_SWAP_FAILED;
    }
    close(dirfd);
    logline("swapped %s into %s/%s", staging, dir, TARGET_NAME);

    /* Tidying, not part of the update. */
    char old[PATH_MAX];
    snprintf(old, sizeof(old), "%s/%s", dir, staging);
    if (removefile(old, NULL, REMOVEFILE_RECURSIVE) != 0) {
        logline("could not remove the old bundle at %s: %s", old, strerror(errno));
    }

    if (want_relaunch) relaunch(dir);
    return EXIT_OK;
}
