# GitHub Copilot OpenTelemetry Setup Guide
**Goal:** Collect telemetry from GitHub Copilot (VS Code + CLI) and send to Azure Application Insights

---

## Step 1: Install Prerequisites

### 1.1 Install Docker Desktop
```bash
# Download and install from:
# https://www.docker.com/products/docker-desktop/

# Verify installation
docker --version
docker ps
```

### 1.2 Install GitHub Copilot CLI
```bash
# Install via npm
npm install -g @githubnext/github-copilot-cli

# Verify installation
copilot version

# Login if needed
copilot login
```

### 1.3 Verify VS Code
- Ensure GitHub Copilot extension is installed in VS Code
- Open Command Palette (Cmd+Shift+P) → "Preferences: Open User Settings (JSON)"

---

## Step 2: Get Application Insights Connection String

### 2.1 Azure Portal
1. Sign in to https://portal.azure.com/
2. Navigate to your **Application Insights** resource
   - Or create new: Search "Application Insights" → Create
   - Attach to a Log Analytics workspace
3. Go to **Overview** page
4. Find **Connection String** in the Essentials panel
5. Click the **Copy** icon

### 2.2 Connection String Format
```
InstrumentationKey=00000000-0000-0000-0000-000000000000;
IngestionEndpoint=https://<region>.in.applicationinsights.azure.com/;
LiveEndpoint=https://<region>.livediagnostics.monitor.azure.com/;
ApplicationId=00000000-0000-0000-0000-000000000000
```

⚠️ **IMPORTANT:** Treat this as a secret - anyone with it can send data to your App Insights

---

## Step 3: Create OpenTelemetry Collector Configuration

### 3.1 Create config file
Create a file named `otel-collector-config.yaml` in your workspace:

```yaml
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318
      grpc:
        endpoint: 0.0.0.0:4317

exporters:
  azuremonitor:
    connection_string: "YOUR_CONNECTION_STRING_HERE"

service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [azuremonitor]
    metrics:
      receivers: [otlp]
      exporters: [azuremonitor]
    logs:
      receivers: [otlp]
      exporters: [azuremonitor]
```

### 3.2 Replace YOUR_CONNECTION_STRING_HERE
Replace the entire connection string from Step 2 (keep the quotes):
```yaml
connection_string: "InstrumentationKey=...;IngestionEndpoint=https://..."
```

---

## Step 4: Run OpenTelemetry Collector

### 4.1 Start the collector
```bash
# Navigate to directory containing otel-collector-config.yaml
cd /path/to/your/config

# Run the collector (automatically restarts after system reboot)
docker run -d \
  --name otel-collector \
  --restart unless-stopped \
  -p 4318:4318 \
  -p 4317:4317 \
  -v $(pwd)/otel-collector-config.yaml:/etc/otelcol-contrib/config.yaml \
  otel/opentelemetry-collector-contrib:latest
```

### 4.2 Verify collector is running
```bash
# Check container status
docker ps | grep otel-collector

# Check logs
docker logs otel-collector

# Should see: "Everything is ready. Begin running and processing data."
```

### 4.3 Test connectivity
```bash
# Test OTLP HTTP endpoint
curl http://localhost:4318/v1/traces
# Should return: "404 page not found" (expected - endpoint is ready but needs POST)
```

---

## Step 5: Configure GitHub Copilot in VS Code

### 5.1 Open VS Code Settings
1. Open Command Palette: `Cmd+Shift+P` (Mac) or `Ctrl+Shift+P` (Windows/Linux)
2. Type: "Preferences: Open User Settings (JSON)"
3. Press Enter

### 5.2 Add OpenTelemetry settings
Add these settings to your `settings.json`:

```json
{
    "github.copilot.chat.otel.enabled": true,
    "github.copilot.chat.otel.exporterType": "otlp-http",
    "github.copilot.chat.otel.otlpEndpoint": "http://localhost:4318",
    "github.copilot.chat.otel.captureContent": true
}
```

**What each setting does:**
- `enabled`: Turn on OpenTelemetry export
- `exporterType`: Use OTLP over HTTP protocol
- `otlpEndpoint`: Where to send telemetry (your local collector)
- `captureContent`: Include prompt text in traces (for detailed debugging)

### 5.3 Restart VS Code
Close and reopen VS Code to apply settings.

---

## Step 6: Configure GitHub Copilot CLI

### 6.1 Set environment variables
Add to your shell profile (`~/.zshrc`, `~/.bashrc`, or `~/.bash_profile`):

```bash
# GitHub Copilot CLI OpenTelemetry
export COPILOT_OTEL_ENABLED=true
export COPILOT_OTEL_EXPORTER=otlp-http
export COPILOT_OTEL_ENDPOINT=http://localhost:4318
export COPILOT_OTEL_CAPTURE_CONTENT=true
```

### 6.2 Reload shell configuration
```bash
# For zsh (default on macOS)
source ~/.zshrc

# For bash
source ~/.bashrc
```

### 6.3 Verify variables are set
```bash
echo $COPILOT_OTEL_ENABLED
# Should output: true
```

---

## Step 7: Verify Telemetry Data Flow

### 7.1 Generate test telemetry
**In VS Code:**
1. Open Copilot Chat (Cmd+Shift+I or sidebar icon)
2. Ask a question: "What is TypeScript?"
3. Wait for response

**In Copilot CLI:**
```bash
copilot
# Then ask: "How do I list files in a directory?"
```

### 7.2 Check collector logs
```bash
docker logs otel-collector --tail 50
# Look for: "Traces", "Metrics" being sent to Azure Monitor
```

### 7.3 Query Application Insights
**In Azure Portal:**
1. Go to your Application Insights resource
2. Click **Logs** in left menu
3. Run this query:

```kql
dependencies
| where cloud_RoleName == "copilot-chat"
| take 10
```

**Should see:** Copilot operations with model names, durations, etc.

**Alternative query for traces:**
```kql
traces
| where cloud_RoleName == "copilot-chat"
| take 10
```

### 7.4 Check metrics
```kql
customMetrics
| where name contains "copilot"
| take 10
```

⏱️ **Note:** Data may take 2-5 minutes to appear in Application Insights

---

## Step 8: Setup Grafana (Optional but Recommended)

### 8.1 Access Grafana
- **Azure Managed Grafana**: Navigate to your Grafana instance in Azure Portal
- **Self-hosted Grafana**: Ensure version 11.6 or later

### 8.2 Add Azure Monitor Data Source
1. Go to **Configuration** → **Data Sources** → **Add data source**
2. Select **Azure Monitor**
3. Configure:
   - **Authentication**: Managed Identity or Service Principal
   - **Default Subscription**: Your Azure subscription
   - Click **Save & Test**

### 8.3 Import GitHub Copilot Dashboard
1. Go to **Dashboards** → **Import**
2. Enter dashboard ID: **25053**
3. Or use URL: https://grafana.com/grafana/dashboards/25053-github-copilot/
4. Click **Load**
5. Select your Azure Monitor data source
6. Configure variables:
   - **Subscription**: Your Azure subscription
   - **Resource Group**: Where App Insights lives
   - **Application Insights**: Your resource name
7. Click **Import**

---

## Step 9: Explore Your Data

### 9.1 In Grafana Dashboard
You'll see:
- **Operations**: Total Copilot operations over time
- **Tokens**: Input/Output token usage by model
- **Latency**: Response duration and Time-to-First-Token (TTFT)
- **Model Usage**: Distribution across GPT-4, o1, etc.
- **Tool Calls**: Which Copilot tools are being used
- **Errors**: Exception tracking
- **Traces**: Drill-down into specific interactions

### 9.2 In Application Insights
**Useful queries:**

**Top models by usage:**
```kql
dependencies
| where cloud_RoleName == "copilot-chat"
| summarize Count = count() by tostring(customDimensions.model)
| order by Count desc
```

**Average token usage:**
```kql
customMetrics
| where name == "copilot.tokens.input" or name == "copilot.tokens.output"
| summarize avg(value) by name, bin(timestamp, 1h)
```

**Slow operations (> 5 seconds):**
```kql
dependencies
| where cloud_RoleName == "copilot-chat"
| where duration > 5000
| project timestamp, operation_Name, duration, customDimensions.model
| order by timestamp desc
```

---

## Step 10: Advanced Configuration (Optional)

### 10.1 Filter sensitive content
If you don't want to capture prompt text, set:

**VS Code:**
```json
"github.copilot.chat.otel.captureContent": false
```

**CLI:**
```bash
export COPILOT_OTEL_CAPTURE_CONTENT=false
```

### 10.2 Multiple environments
Create separate collectors for dev/prod:
```bash
# Dev collector on port 4318
docker run -d --name otel-collector-dev -p 4318:4318 ...

# Prod collector on port 4319
docker run -d --name otel-collector-prod -p 4319:4318 ...
```

Point different machines at different endpoints.

### 10.3 Collector resource attributes
Add resource attributes to identify your team/machine:

```yaml
processors:
  resource:
    attributes:
      - key: service.name
        value: copilot-telemetry
        action: upsert
      - key: deployment.environment
        value: dev
        action: upsert
      - key: team
        value: your-team-name
        action: upsert

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [resource]
      exporters: [azuremonitor]
```

---

## Troubleshooting

### Collector not receiving data
```bash
# Check if collector is listening
netstat -an | grep 4318

# Check firewall
# Ensure localhost:4318 is accessible

# Test with curl
curl -X POST http://localhost:4318/v1/traces \
  -H "Content-Type: application/json" \
  -d '{}'
```

### No data in Application Insights
```bash
# Verify collector can reach Azure
docker logs otel-collector | grep -i error

# Check connection string is correct
docker exec otel-collector cat /etc/otelcol-contrib/config.yaml

# Verify ingestion endpoint is reachable
curl https://<region>.in.applicationinsights.azure.com/
```

### VS Code not sending telemetry
1. Check settings.json is valid JSON
2. Reload VS Code window: Cmd+Shift+P → "Reload Window"
3. Check Copilot output: View → Output → "GitHub Copilot"

### CLI not sending telemetry
```bash
# Verify environment variables
env | grep COPILOT

# Start copilot with debug logging
COPILOT_LOG_LEVEL=debug copilot
```

---

## Cost Considerations

### Application Insights Pricing
- First 5 GB/month: Included
- Additional data: ~$2.30/GB (varies by region)
- Average Copilot usage: ~10-50 MB/day per developer

**Estimate:** 10 developers × 30 MB/day × 30 days = 9 GB/month ≈ $9/month

### Ways to reduce data
1. Disable `captureContent` (reduces log volume)
2. Sample traces in collector:
```yaml
processors:
  probabilistic_sampler:
    sampling_percentage: 10  # Only keep 10% of traces

service:
  pipelines:
    traces:
      processors: [probabilistic_sampler]
```

---

## Next Steps

### Experiment with RPIV Pattern (from Shawn's post)
1. **Reasoning**: Use Opus/Sonnet for planning
2. **Planning**: Collect spec as "contract"
3. **Implementation**: Use MAI (cheaper, faster)
4. **Verification**: Use Sonnet to review

Track each stage's model usage and cost in Grafana!

### Use Observability Agent
Explore flows directly in App Insights:
- Transaction search for end-to-end traces
- Application Map for dependencies
- Performance blade for slow operations

---

## Quick Reference

### Collector Commands
```bash
# Start
docker start otel-collector

# Stop
docker stop otel-collector

# Restart (after config change)
docker restart otel-collector

# View logs
docker logs -f otel-collector

# Remove and recreate
docker rm -f otel-collector
# Then re-run the docker run command
```

### Configuration Files
- **Collector**: `otel-collector-config.yaml`
- **VS Code**: `~/Library/Application Support/Code/User/settings.json` (Mac)
- **CLI**: `~/.zshrc` or `~/.bashrc`

### Key URLs
- Application Insights: https://portal.azure.com/
- Grafana Dashboard: https://grafana.com/grafana/dashboards/25053
- Documentation: https://learn.microsoft.com/en-us/azure/managed-grafana/grafana-opentelemetry-app-insights

---

**You're all set!** Follow these steps in order, and you'll have full observability into your GitHub Copilot usage. 🚀
