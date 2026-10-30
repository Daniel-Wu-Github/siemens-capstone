# Microsoft Teams Integration Setup Guide

This guide explains how to connect stock alert notifications to a Microsoft Teams channel using an incoming webhook or Power Automate Workflows.

## 1. Creating a Webhook URL in Microsoft Teams

### Option A: Workflows (Recommended by Microsoft)
1. In Microsoft Teams, navigate to the channel where you want alerts posted.
2. Click the channel's `...` (More options) menu and select **Workflows** (or search for Workflows in Apps).
3. Choose the template **"Post to a channel when a webhook request is received"**.
4. Name the workflow (e.g. `Siemens Stock Alerts`) and select the Team and Channel.
5. Click **Next** / **Create flow**.
6. Copy the generated webhook URL (looks like `https://prod-XX.eastus.logic.azure.com:443/workflows/...`).

### Option B: Incoming Webhook Connector (Classic)
1. In Teams, click the `...` next to the channel name and select **Connectors**.
2. Locate **Incoming Webhook** and click **Configure** (or **Add** then **Configure**).
3. Provide a name (e.g. `Stock Alert Bot`) and optionally upload an icon.
4. Click **Create** and copy the resulting webhook URL (looks like `https://siemens.webhook.office.com/webhookb2/...`).
5. Click **Done**.

## 2. Configuring the Application

1. Set the `TEAMS_WEBHOOK_URL` environment variable before starting the application:
   ```sh
   export TEAMS_WEBHOOK_URL="https://example.webhook.office.com/webhookb2/your-webhook-id"
   ```
   Or include it in your environment configuration / LaunchAgent definition.

2. Start (or restart) the server:
   ```sh
   cd /path/to/siemens-capstone
   .venv/bin/python server.py --db data/capstone.sqlite3
   ```

3. Verify connection in the UI:
   - Go to the **Stock alerts** page (`#stock`).
   - Look at the **Delivery** card in the right column: the Teams channel will display **Connected** (instead of *Not configured*).

## 3. Testing Notification Delivery

1. On the **Stock alerts** page, click the **Send digest** button in the header.
2. Check your Teams channel for the incoming Adaptive Card:
   - **Card Title:** Stock alert summary / notification subject.
   - **Metrics:** Counts of new breached positions, recovered positions, and open alerts.
   - **Position List:** Material descriptions, supermarket location, bin identifier, available stock, and minimum limit.
   - **Action:** A button to open the dashboard position in the browser.

## 4. Removing or Disabling Teams Notifications

To disable Teams notifications:
1. Unset the environment variable:
   ```sh
   unset TEAMS_WEBHOOK_URL
   ```
2. Restart the server.
3. To permanently delete the endpoint in Teams, open the channel's **Workflows** or **Connectors**, select your configured webhook, and click **Delete**.
