FROM python:3.14-slim

WORKDIR /workspace

RUN useradd -m sandboxuser

USER sandboxuser

COPY requirements.txt /workspace/requirements.txt

RUN pip install --no-cache-dir -r requirements.txt

COPY workspace/ /workspace/

CMD ["python", "buggy.py"]
