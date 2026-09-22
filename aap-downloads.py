#!/usr/bin/env python3
"""List Ansible Automation Platform image downloads available via the RHSM API.

Authentication: uses the RH_OFFLINE_TOKEN environment variable to obtain
a short-lived access token from the Red Hat SSO service.

API reference: https://access.redhat.com/management/api/rhsm
"""

import argparse
import os
import sys
import requests

TOKEN_URL = "https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token"
BASE_URL = "https://api.access.redhat.com/management/v1"


def get_access_token(offline_token: str) -> str:
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": "rhsm-api",
            "refresh_token": offline_token,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def content_set_label(aap_version: str, rhel_version: str, arch: str) -> str:
    return f"ansible-automation-platform-{aap_version}-for-rhel-{rhel_version}-{arch}-files"


def get_subscriptions(session: requests.Session) -> list[dict]:
    subscriptions = []
    offset = 0
    limit = 50
    while True:
        resp = session.get(
            f"{BASE_URL}/subscriptions",
            params={"limit": limit, "offset": offset},
            timeout=30,
        )
        resp.raise_for_status()
        page = resp.json().get("body", [])
        subscriptions.extend(page)
        offset += len(page)
        if len(page) < limit:
            break
    return subscriptions


def get_aap_file_content_sets(session: requests.Session) -> list[str]:
    """Return sorted unique labels of all AAP FILE-type content sets across all subscriptions.

    The contentSets API caps at 1000 records per page, so pagination is required
    to reach AAP entries in large subscriptions (e.g. Employee SKU has 11k+ content sets).
    """
    subscriptions = get_subscriptions(session)
    seen: set[str] = set()
    for sub in subscriptions:
        offset = 0
        limit = 1000
        while True:
            resp = session.get(
                f"{BASE_URL}/subscriptions/{sub['subscriptionNumber']}/contentSets",
                params={"limit": limit, "offset": offset},
                timeout=30,
            )
            if not resp.ok:
                break
            page = resp.json().get("body", [])
            for cset in page:
                label = cset.get("label", "")
                if cset.get("type") == "FILE" and label.startswith("ansible-automation-platform-"):
                    seen.add(label)
            offset += len(page)
            if len(page) < limit:
                break
    return sorted(seen)


def get_images_for_content_set(session: requests.Session, label: str) -> list[dict]:
    images = []
    offset = 0
    limit = 100
    while True:
        resp = session.get(
            f"{BASE_URL}/images/cset/{label}",
            params={"limit": limit, "offset": offset},
            timeout=30,
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        page = resp.json().get("body", [])
        images.extend(page)
        offset += len(page)
        if len(page) < limit:
            break
    return images


def download_image(session: requests.Session, image: dict, dest_dir: str = ".") -> None:
    filename = image.get("filename", "download")
    dest = os.path.join(dest_dir, filename)
    href = image.get("downloadHref")
    if not href:
        print(f"Error: no download URL available for {filename}", file=sys.stderr)
        sys.exit(1)

    # The download endpoint returns a 307 redirect to the CDN; follow it.
    resp = session.get(href, stream=True, allow_redirects=True, timeout=60)
    resp.raise_for_status()

    total = int(resp.headers.get("content-length", 0))
    received = 0
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
            received += len(chunk)
            if total:
                pct = received * 100 // total
                print(f"\r  {pct:3d}%  {received // (1024*1024)} / {total // (1024*1024)} MB", end="", flush=True)
    print(f"\r  Saved: {dest}{' ' * 20}")


def print_image(image: dict, content_set_label: str) -> None:
    print(f"  File        : {image.get('filename', '(unknown)')}")
    print(f"  Name        : {image.get('imageName', '')}")
    print(f"  Content Set : {content_set_label}")
    print(f"  Arch        : {image.get('arch', '')}")
    print(f"  Published   : {image.get('datePublished', '')}")
    print(f"  SHA256      : {image.get('checksum', '')}")
    if href := image.get("downloadHref"):
        print(f"  Download    : {href}")
    print()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interact with Ansible Automation Platform downloads on access.redhat.com."
    )
    parser.add_argument("--aap-version", "--aap", default="2.7", metavar="VERSION",
                        help="AAP version (default: 2.7)")
    parser.add_argument("--rhel-version", "--rhel", default="10", metavar="VERSION",
                        help="RHEL version (default: 10)")
    parser.add_argument("--arch", default="x86_64", metavar="ARCH",
                        help="CPU architecture (default: x86_64)")
    parser.add_argument("--content-set", "--cset", metavar="LABEL",
                        help="Explicit content set label (default: ansible-automation-platform-2.7-for-rhel-10-x86_64-files)")
    parser.add_argument("--list", action="store_true",
                        help="List all file downloads in the specified content set")
    parser.add_argument("--list-content-sets", "--list-csets", action="store_true",
                        help="List all AAP content sets available in your subscriptions")
    parser.add_argument("--download", metavar="FILENAME",
                        help="Download a file by filename from the resolved content set")
    return parser.parse_args()


def resolve_label(args: argparse.Namespace) -> str:
    if args.content_set:
        return args.content_set
    return content_set_label(args.aap_version, args.rhel_version, args.arch)


def main() -> None:
    args = parse_args()

    if not args.list and not args.list_content_sets and not args.download:
        print("No action specified. Use --help for usage.")
        sys.exit(0)

    offline_token = os.environ.get("RH_OFFLINE_TOKEN")
    if not offline_token:
        print("Error: RH_OFFLINE_TOKEN environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    print("Authenticating...")
    try:
        access_token = get_access_token(offline_token)
    except requests.HTTPError as e:
        print(f"Authentication failed: {e}", file=sys.stderr)
        sys.exit(1)

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    })

    if args.list_content_sets:
        print("Scanning subscriptions for AAP download content sets (this may take a few minutes)...")
        try:
            labels = get_aap_file_content_sets(session)
        except requests.HTTPError as e:
            print(f"Failed to scan content sets: {e}", file=sys.stderr)
            sys.exit(1)
        if not labels:
            print("No AAP download content sets found in your subscriptions.")
            sys.exit(0)
        print(f"\n{len(labels)} AAP download content set(s):\n")
        for label in labels:
            print(f"  {label}")
        return

    label = resolve_label(args)
    print(f"Fetching downloads for content set: {label}\n")
    try:
        images = get_images_for_content_set(session, label)
    except requests.HTTPError as e:
        print(f"Failed to fetch downloads: {e}", file=sys.stderr)
        sys.exit(1)

    if not images:
        print("No downloads found. The content set may not exist or your account may not be entitled.")
        sys.exit(0)

    if args.download:
        matches = [img for img in images if img.get("filename") == args.download]
        if not matches:
            print(f"Error: '{args.download}' not found in content set {label}.", file=sys.stderr)
            print("Use --list to see available files.", file=sys.stderr)
            sys.exit(1)
        print(f"Downloading: {args.download}")
        try:
            download_image(session, matches[0])
        except requests.HTTPError as e:
            print(f"Download failed: {e}", file=sys.stderr)
            sys.exit(1)
        return

    print(f"{'='*60}")
    print(f"{label}: {len(images)} file(s)")
    print(f"{'='*60}\n")

    for image in images:
        print_image(image, label)


if __name__ == "__main__":
    main()
