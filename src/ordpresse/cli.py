"""Command Line Interface for Den Danske Ordpresse (ordpresse)."""

import argparse
import sys
from pathlib import Path
import uvicorn

from ordpresse.config import (
    DEFAULT_BOOKNAME,
    DEFAULT_HOST,
    DEFAULT_PORT,
    OUTPUT_DIR,
)
from ordpresse.datasets import DatasetImporter
from ordpresse.pipeline import Pipeline


def fetch_datasets_cmd(args: argparse.Namespace) -> int:
    """Download and stream-import COR and ODS datasets into SQLite."""
    print("=== Den Danske Ordpresse: Henter og indekserer datasæt ===")
    importer = DatasetImporter()
    
    def on_progress(msg: str):
        print(f"[*] {msg}")

    importer.download_datasets(progress_cb=on_progress)
    importer.import_all(progress_cb=on_progress)
    print("=== Indeksering fuldført! Datasets.sqlite3 er opdateret. ===")
    return 0


def build_cmd(args: argparse.Namespace) -> int:
    """Build StarDict dictionary from EPUBs or word lists."""
    print("=== Den Danske Ordpresse: Bygger StarDict-ordbog ===")
    
    epub_paths = [Path(p) for p in args.epub] if args.epub else None
    custom_words = args.words if args.words else None
    
    pipeline = Pipeline(
        output_dir=Path(args.output),
        bookname=args.bookname,
    )
    
    def on_progress(msg: str, pct: int):
        print(f"[{pct:3d}%] {msg}")

    result = pipeline.run(
        epub_files=epub_paths,
        custom_words=custom_words,
        include_seeds=True,
        full_dictionary=args.full,
        allow_network=not args.no_network,
        progress_cb=on_progress,
    )
    
    print("\n" + "=" * 50)
    print(f"Ordbog bygget med succes!")
    print(f"Bygge-ID:    {result.build_id}")
    print(f"Hovedord:    {result.entry_count}")
    print(f"Synonymer:   {result.synonym_count}")
    print(f"ZIP-fil:     {result.zip_path}")
    print("=" * 50)
    return 0


def serve_cmd(args: argparse.Namespace) -> int:
    """Run the FastAPI web application server."""
    print(f"Starter Ordpresse webserver på http://{args.host}:{args.port}")
    uvicorn.run(
        "ordpresse.web.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    return 0


def triage_cmd(args: argparse.Namespace) -> int:
    """Execute triage on cache.sqlite3."""
    from ordpresse.triage import run_triage
    print(f"=== Kører Ordpresse Cache Triage på {args.db} ===")
    res = run_triage(
        cache_db_path=Path(args.db),
        purged_output_path=Path(args.purged_file) if args.purged_file else None,
        dry_run=args.dry_run,
    )
    print(f"Før triage:       {res.total_before} rækker")
    print(f"KEEP (rene):      {res.keep_count}")
    print(f"SALVAGED:         {res.salvaged_count}")
    print(f"PURGED (slettet): {res.purged_count}")
    if res.salvaged_examples:
        print("\n--- Eksempler på SALVAGED rækker ---")
        for w, before, after in res.salvaged_examples:
            print(f"• '{w}':")
            print(f"    Før:  {before}")
            print(f"    Efter: {after}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ordpresse",
        description="Den Danske Ordpresse — Historisk og moderne dansk StarDict-ordbogsbygger til KOReader",
    )
    subparsers = parser.add_subparsers(dest="command", help="Kommandoer")

    # triage
    sub_triage = subparsers.add_parser("triage", help="Kør cache triage (keep, salvage, purge)")
    sub_triage.add_argument("--db", default="data/cache.sqlite3", help="Sti til cache.sqlite3")
    sub_triage.add_argument("--purged-file", default="data/purged_words.txt", help="Sti til output for slettede ord")
    sub_triage.add_argument("--dry-run", action="store_true", help="Analysér uden at ændre databasen")
    sub_triage.set_defaults(func=triage_cmd)

    # fetch-datasets
    sub_fetch = subparsers.add_parser("fetch-datasets", help="Hent officielle COR og ODS datasæt og byg SQLite")
    sub_fetch.set_defaults(func=fetch_datasets_cmd)

    # build
    sub_build = subparsers.add_parser("build", help="Byg en StarDict-ordbog (.idx, .ifo, .dict, .syn)")
    sub_build.add_argument("--epub", nargs="+", help="Sti til en eller flere .epub filer", default=[])
    sub_build.add_argument("--words", nargs="+", help="Ekstra specifikke ord til ordbogen", default=[])
    sub_build.add_argument("--full", action="store_true", help="Byg komplet ordbog over alle indekserede ord fra COR og ODS")
    sub_build.add_argument("--output", help="Output mappe", default=str(OUTPUT_DIR))
    sub_build.add_argument("--bookname", help="Ordbogens titel i KOReader", default=DEFAULT_BOOKNAME)
    sub_build.add_argument("--no-network", action="store_true", help="Kør offline uden ekstern scraping")
    sub_build.set_defaults(func=build_cmd)

    # serve
    sub_serve = subparsers.add_parser("serve", help="Start FastAPI webbrugerfladen")
    sub_serve.add_argument("--host", default=DEFAULT_HOST, help=f"Host (standard: {DEFAULT_HOST})")
    sub_serve.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port (standard: {DEFAULT_PORT})")
    sub_serve.add_argument("--reload", action="store_true", help="Genindlæs automatisk ved kodeændringer")
    sub_serve.set_defaults(func=serve_cmd)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
