import json
import os
from datetime import datetime


def ensure_dir(dir_path):
    os.makedirs(dir_path, exist_ok=True)


def join_path(*segments):
    return os.path.join(*segments)


def ensure_output_dirs(paths_config):
    ensure_dir(paths_config["screenshotDir"])
    ensure_dir(paths_config["resultsDir"])


def read_json_file(file_path):
    with open(file_path, "r", encoding="utf-8") as file:
        return json.load(file)


def write_json_file(file_path, data):
    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def build_timestamp_for_file_name(date=None):
    value = date or datetime.now()
    yyyy = value.strftime("%Y")
    mm = value.strftime("%m")
    dd = value.strftime("%d")
    hh = value.strftime("%H")
    minute = value.strftime("%M")
    ss = value.strftime("%S")
    return f"{yyyy}-{mm}-{dd}_{hh}-{minute}-{ss}"
