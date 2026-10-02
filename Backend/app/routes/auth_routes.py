import os
import unicodedata

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, StrictStr
from auth_utils import _check_token

# Where docker-compose.yml binds the host workspace. The container cannot learn
# its own bind SOURCE — from inside, only this path exists — so identity has to
# be established by comparing something both sides can observe.
CONTAINER_WORKSPACE_MOUNT = "/workspace"

# The fingerprint itself lives in `workspace_fingerprint`, which imports
# nothing but the standard library. That is not tidiness: the cross-language
# parity test has to run this exact code, and while it lived here it could only
# be reached through FastAPI, so the test demanded the backend virtualenv and
# skipped itself in a CI job that has none (AUDIT R6-02, 31 Aug 2026).
#
# The private aliases are kept because the endpoint tests reach for them by
# these names, and renaming them here would be churn that proves nothing.
from workspace_fingerprint import (  # noqa: E402
    WORKSPACE_FINGERPRINT_ALGO,
    NO_DESCEND as _NO_DESCEND,
    kind as _kind,
    fingerprint_lines as _fingerprint_lines,
    fingerprint_digest as _fingerprint_digest,
)


class DisplayNameBody(BaseModel):
    name: StrictStr


def create_auth_router(db):
    router = APIRouter()

    @router.get("/me")
    async def get_me(x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        name = db.get_setting("user_display_name")
        name = name if isinstance(name, str) and name else "local"
        # user_id, username, name, avatar — eski frontend uyumluluğu
        return {"user_id": 1, "id": 1, "username": "local", "name": name,
                "email": "local@localhost", "avatar": ""}

    @router.put("/me/name")
    async def put_name(body: DisplayNameBody,
                       x_session_token: str = Header(alias="X-Session-Token", default="")):
        _check_token(x_session_token)
        # Minor audit fixes, 2 Oct 2026: preserve word separators before dropping controls.
        name = "".join(" " if char.isspace() else char for char in body.name)
        name = "".join(char for char in name if unicodedata.category(char) not in {"Cc", "Cf"})
        name = " ".join(name.split())
        if len(name) > 40:
            raise HTTPException(status_code=422, detail="name_too_long")
        if name.lower() == "local":
            name = ""
        db.set_setting("user_display_name", name)
        return {"name": name}

    @router.get("/health/auth")
    async def health_auth(x_session_token: str = Header(alias="X-Session-Token", default="")):
        """Liveness, but only for a caller holding the app token.

        `/health` is deliberately unauthenticated (see the authz matrix's
        whitelist), which makes it useless for the one question Docker mode has
        to ask at startup: is the backend I just reached holding the SAME secret
        I am? Measured 31 Aug 2026 — a container kept alive by `restart:
        unless-stopped` outlives the shell that exported its token, answers
        `/health` happily, and then 401s every real call. Every visible startup
        signal read as fine.

        It lives here rather than beside `/health` in `main.py` on purpose: the
        authz matrix installs its sentinel into `routes.*` modules, so a
        protected endpoint declared outside them is invisible to the one test
        that proves the gate is actually called.
        """
        _check_token(x_session_token)
        return {"status": "ok", "service": "gamachine", "auth": "ok"}

    @router.get("/health/workspace")
    async def health_workspace(x_session_token: str = Header(alias="X-Session-Token", default="")):
        """A read-only fingerprint of the tree this backend serves at `/workspace`.

        WHAT IT IS FOR
            `restart: unless-stopped` keeps a container alive across the shell
            that started it, and a container's bind source is fixed when the
            container is CREATED. So exporting a new `GAMACHINE_WORKSPACE` and
            relaunching Electron changes what Electron believes is mounted and
            changes nothing about what is actually mounted. Electron then maps
            project B onto `/workspace` successfully while the live backend
            reads and writes project A, both halves reporting success.
            `/health/auth` does not cover this: it proves the two sides share a
            token and says nothing about which tree is behind the mount.

        WHAT A COMPARISON PROVES, AND WHAT IT DOES NOT
            The Electron side computes the same fingerprint over the host
            directory it thinks is mounted and compares.

            A MISMATCH is strong: these two directories do not currently have
            the same two-level layout, so they are not the same tree — unless
            something changed in the tree between the two samples, which is why
            the caller confirms a mismatch a second time before refusing.

            A MATCH is much weaker, and this is the honest limit. It says the
            two directories agree on entry names and kinds two levels deep. It
            does NOT prove they are the same directory: two copies of one
            project, or two projects generated from the same template and not
            yet edited, hash identically. File CONTENT is not sampled at all, so
            a tree whose files all differ but whose layout matches passes.

            No stronger read-only check was available. Comparing st_dev/st_ino
            across the mount would be conclusive on a Linux bind mount, but
            Docker Desktop's virtualised filesystem synthesises inode numbers,
            so on the platforms this project is developed on it would report a
            mismatch for the correct tree. Writing a marker file into the
            workspace would be conclusive everywhere and is deliberately not
            done: a Unity project reacts to new files by importing them.

        It is read-only: `os.scandir` plus dirent kind bits, no `open`, no
        write, no mtime read. And it is behind the same gate as everything else
        — a directory listing of the developer's project is not public.
        """
        _check_token(x_session_token)
        base = {"status": "ok", "algo": WORKSPACE_FINGERPRINT_ALGO,
                "mount": CONTAINER_WORKSPACE_MOUNT}
        if not os.path.isdir(CONTAINER_WORKSPACE_MOUNT):
            # Reported rather than raised. The caller needs to tell "no mount"
            # apart from "wrong mount": they have different fixes, and this
            # endpoint also answers on the non-Docker path where there is no
            # mount and nothing is wrong.
            return {**base, "mounted": False, "entries": 0, "fingerprint": ""}
        lines = _fingerprint_lines(CONTAINER_WORKSPACE_MOUNT)
        return {**base, "mounted": True, "entries": len(lines),
                "fingerprint": _fingerprint_digest(lines)}

    @router.post("/login")
    async def login():
        # Geriye dönük uyumluluk stub (token gerektirmez)
        return {"session_token": "local",
                "user": {"user_id": 1, "username": "local", "email": "local@localhost"}}

    @router.post("/logout")
    async def logout():
        return {"ok": True}

    @router.get("/auth/providers")
    async def get_providers():
        # Eski frontend bu shape'i bekliyordu
        return {"google": False, "github": False}

    return router
