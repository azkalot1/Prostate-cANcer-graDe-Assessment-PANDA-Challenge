from panda_challenge import runTrainingClassifcationMultiCrop
import json
import sys

if __name__ == '__main__':
    with open(sys.argv[1], 'r') as f:
        params = json.load(f)
    runTrainingClassifcationMultiCrop(params)
