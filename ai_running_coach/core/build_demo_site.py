"""Build a deterministic public example without exporting any working files."""
import argparse
from pathlib import Path
import tempfile

from demo_data import SAMPLE_DATE
from setup_coach import demo


def build_demo_site(output):
    output=Path(output)
    if output.is_symlink() or (output.exists() and (not output.is_dir() or any(output.iterdir()))):
        raise ValueError('Public demo output must be a new or empty directory, not a symlink')
    # The only input data are authored by demo_data in our own temporary folder.
    # Never accept a source-workspace argument or copy arbitrary workspace files.
    with tempfile.TemporaryDirectory(prefix='coach-public-demo-') as directory:
        page=demo(Path(directory)/'synthetic',today=SAMPLE_DATE)
        content=page.read_bytes()
    output.mkdir(parents=True,exist_ok=True)
    (output/'index.html').write_bytes(content)
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True,help='New/empty directory containing only the static public example')
    args=parser.parse_args()
    print('Public synthetic example: '+str(build_demo_site(args.output).resolve()))


if __name__=='__main__':main()
