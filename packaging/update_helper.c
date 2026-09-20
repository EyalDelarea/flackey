/*
 * flackey-update-helper -- swap a staged Flackey.app in for the installed one.
 *
 * The app stages the new bundle beside the old one, copies this program out of itself, spawns it
 * detached and quits. This program waits for that and then performs the one destructive operation:
 *
 *     renameatx_np(dir, ".Flackey-staging-XXXXXX.app", dir, "Flackey.app", RENAME_SWAP)
 *
 * RENAME_SWAP is why this is compiled C and not a shell script. Two `mv`s leave a window in which
 * /Applications/Flackey.app does not exist, and a machine that loses power in that window has no app.
 *
 * Everything above the swap is input validation, and it is the security boundary rather than a
 * formality: this runs detached, unwatched, against a path in /Applications. Anything validated by
 * path could be swapped between the check and the call, so every check and the swap itself are
 * relative to one directory descriptor opened once.
 *
 * Nothing here runs as root: no privileged component, no XPC service, no socket. See
 * docs/specs/2026-09-19-issue-58-update-design.md.
 *
 * Built by packaging/build_app.sh:
 *     clang -O2 -Wall -Wextra -o flackey-update-helper packaging/update_helper.c
 */

#include <ctype.h>
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

/* Read back only by a human in the log; the app is gone by the time any of them happen. They exist so
 * that "refused" and "tried and failed" are distinguishable at a glance. */
enum {
    EXIT_OK = 0,
    EXIT_USAGE = 2,
    EXIT_REFUSED = 3,      /* a validation rule said no; nothing was touched */
    EXIT_SWAP_BLOCKED = 4, /* the kernel refused the swap -- App Management, most likely */
    EXIT_SWAP_FAILED = 5,
    EXIT_PARENT_ALIVE = 6,
};

/* The name is the only thing tying the directory about to be swapped into /Applications to one this
 * app created minutes ago, and it is checked here rather than trusted because an argument is exactly
 * what an attacker would control. */
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

/* Never `stat`: a symlink at either name is a refusal, not something to follow. Following one is the
 * classic way an updater is talked into writing outside the directory it was given. */
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

/* A search rather than a parse: Info.plist may be XML or binary, and linking CoreFoundation into a
 * helper that must stay trivially auditable to read two strings is a bad trade. This is a sanity check
 * that the staged directory is a Flackey bundle; the properties doing security work are the ones above
 * it -- no symlink, owned by us, named with a suffix we generated. */
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

/* Swapping in a directory with no executable would leave /Applications/Flackey.app pointing at
 * something unlaunchable, which the owner cannot recover from without a terminal. */
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

/* Returns 1 once the pid is gone, 0 if it outlived the timeout.
 *
 * Refuse rather than proceed on a timeout: swapping under a still-running app would work, but the
 * relaunch afterwards would put a second copy on screen, and two Flackeys sharing one database is
 * worse than an update that did not happen. */
static int wait_for_exit(pid_t pid) {
    struct timespec nap = {.tv_sec = 0, .tv_nsec = WAIT_POLL_MS * 1000000L};
    for (int waited = 0; waited < WAIT_TIMEOUT_MS; waited += WAIT_POLL_MS) {
        if (kill(pid, 0) != 0 && errno == ESRCH) return 1;
        nanosleep(&nap, NULL);
    }
    return 0;
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
    if (argc < 5 || argc > 6) {
        fprintf(stderr, "usage: %s <parent-pid> <directory> <staging-name> <relaunch 0|1> [logfile]\n",
                argv[0]);
        return EXIT_USAGE;
    }
    const char *dir = argv[2];
    const char *staging = argv[3];
    int want_relaunch = strcmp(argv[4], "1") == 0;
    if (argc == 6) logfile = fopen(argv[5], "a");

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

    /* Opened once, and every check and the swap below are relative to it: after this the kernel never
     * resolves the string `dir` again, so nothing swapped in underneath it can redirect what happens. */
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

    /* A staging directory owned by anyone else was not created by this app, whatever it is called.
     * Compared against the real uid: this program is never setuid and must not become useful to
     * anyone who arranges for it to be. */
    if (staged_st.st_uid != getuid()) {
        logline("refusing: %s is owned by uid %u, not %u", staging, staged_st.st_uid, getuid());
        return EXIT_REFUSED;
    }

    int stagefd = openat(dirfd, staging, O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
    if (stagefd < 0) {
        logline("refusing: cannot open %s: %s", staging, strerror(errno));
        return EXIT_REFUSED;
    }
    /* The app strips com.apple.quarantine after unpacking. If it is still here an assumption broke
     * upstream, and a quarantined ad-hoc bundle is refused outright by Gatekeeper -- "Flackey is
     * damaged and can't be opened" -- so swapping it in would brick the install rather than update it. */
    if (has_quarantine(stagefd)) {
        logline("refusing: %s still carries com.apple.quarantine", staging);
        close(stagefd);
        return EXIT_REFUSED;
    }
    if (!declares_flackey_bundle_id(stagefd) || !has_runnable_executable(stagefd)) {
        close(stagefd);
        return EXIT_REFUSED;
    }
    close(stagefd);

    /* The target name is the compiled-in constant rather than an argument: there is exactly one path
     * this program is allowed to overwrite, and it is not negotiable from the command line. */
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

    /* Tidying, not part of the update: a failure here leaves a hidden directory in /Applications and
     * nothing else. */
    char old[PATH_MAX];
    snprintf(old, sizeof(old), "%s/%s", dir, staging);
    if (removefile(old, NULL, REMOVEFILE_RECURSIVE) != 0) {
        logline("could not remove the old bundle at %s: %s", old, strerror(errno));
    }

    if (want_relaunch) relaunch(dir);
    return EXIT_OK;
}
