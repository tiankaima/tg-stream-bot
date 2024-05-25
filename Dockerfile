# basic python docker image:
FROM python:3.12-bookworm

# install ffmpeg, aria2:
RUN apt-get update && apt-get install -y ffmpeg aria2

# copy the requirements file:
COPY requirements.txt /app/requirements.txt
COPY aria2.conf /app/aria2.conf

# set the working directory:
WORKDIR /app

# install the requirements:
RUN pip install -r requirements.txt

# copy main.py:
COPY main.py /app/main.py

# run the application:
CMD ["python", "main.py"]