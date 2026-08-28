FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Shanghai

# Install Xvfb, curl and tzdata
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    curl \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Set timezone to Asia/Shanghai
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app

# Install uv for fast dependency management
RUN pip install --no-cache-dir uv

# Copy project definition and source code
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY config.example.yaml ./

# Install project dependencies
RUN uv pip install --system -e .
RUN playwright install chromium

# Create volume directories
RUN mkdir -p /app/chrome_profile /app/notes

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["sync"]
