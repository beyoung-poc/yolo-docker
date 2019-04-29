FROM nvidia/cuda
MAINTAINER burnsca@amazon.com


# Update base
RUN apt-get update

# Set Environment variables here to avoid a dialog
ENV TZ=America/Chicago

RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Install dependencies
RUN apt-get install -y python3 python3-pip
RUN apt-get install -y python3-opencv
RUN apt-get install -y nginx ca-certificates


ENV PATH="/opt/program:${PATH}"

# Install python package dependencies
RUN pip3 install flask boto3 gputil gevent gunicorn


# move contents to app dir
COPY yolo /opt/program
WORKDIR /opt/program

# Expose port 5000 for Flask
EXPOSE 5000

# Start the app
CMD python3 ./yolov3_app.py


