"""
Alternative entry point: run the pipeline for a single config chosen (or built)
interactively via setup.py. run.py runs every config in configs/ instead.
"""
from setup import setup_config
from run import run_pipeline
from pipeline.data_generator import generate_data


def main():
    config = setup_config()
    generate_data()
    run_pipeline(config)


if __name__ == "__main__":
    main()
