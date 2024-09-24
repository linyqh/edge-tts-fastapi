# Use an official Python runtime as a parent image
FROM python:3.10-slim-bullseye

# Set the working directory in the container
WORKDIR /tts

# 设置/NarratoAI目录权限为777
RUN chmod 777 /tts

ENV PYTHONPATH="/tts"

# Install system dependencies
RUN apt-get update && apt-get install -y \
    vim \
    wget \
    mp3gain \
    && rm -rf /var/lib/apt/lists/*

# Copy only the requirements.txt first to leverage Docker cache
COPY requirements.txt ./

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt && pip install boto3

# Now copy the rest of the codebase into the image
COPY . .

# 公开运行应用的端口
# 获取环境变量 PORT
EXPOSE 8000

# uvicorn main:app --host 0.0.0.0
#CMD ["uvicorn", "main:app", "--workers", "2", "--host", "0.0.0.0", "--port", "8000"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
