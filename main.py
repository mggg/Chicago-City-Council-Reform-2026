from setup import setup_config
from pipeline.simulate_elections import simulate_elections

def main():
    print("Hello from kansas-city-alternative-election-analysis!")
    config = setup_config()
    simulate_elections(config)



if __name__ == "__main__":
    main()
