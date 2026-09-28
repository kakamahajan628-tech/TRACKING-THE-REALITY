import argparse
import asyncio
from pathlib import Path
from dotenv import load_dotenv
from .config import Settings
from .scanner import Scanner
from .reports import html_report,json_text,csv_report,report_bundle,summary,markets_csv,technical_csv


async def once(args):
    settings=Settings.env()
    settings.demo=args.demo
    settings.enable_ws=False
    if args.coins:
        settings.coins=args.coins.upper().split(',')
        settings.__post_init__()
    scanner=Scanner(settings)
    try:
        report=await scanner.scan()
        output=Path(args.output)
        output.mkdir(parents=True,exist_ok=True)
        (output/'report.html').write_text(html_report(report),encoding='utf-8')
        (output/'report.json').write_text(json_text(report),encoding='utf-8')
        (output/'metrics.csv').write_text(csv_report(report),encoding='utf-8')
        (output/'technical.csv').write_text(technical_csv(report),encoding='utf-8')
        if report.get('market_universe',{}).get('enabled'):
            (output/'markets.csv').write_text(markets_csv(report),encoding='utf-8')
        (output/'atlas-136.zip').write_bytes(report_bundle(report))
        print(summary(report))
        print(f'\nSaved complete report to {output.resolve()}')
    finally:await scanner.close()


def main():
    load_dotenv()
    parser=argparse.ArgumentParser(description='Atlas 136: one-shot research scan, no Telegram messages')
    parser.add_argument('--demo',action='store_true',help='Use clearly labelled deterministic synthetic fixtures')
    parser.add_argument('--coins',help='Comma-separated 1–20 symbols')
    parser.add_argument('--output',default='output/scan')
    args=parser.parse_args()
    asyncio.run(once(args))

if __name__=='__main__':main()
