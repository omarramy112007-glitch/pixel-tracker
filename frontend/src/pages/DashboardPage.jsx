// File: frontend/src/pages/DashboardPage.jsx

import React, { useEffect, useMemo, useState } from "react";

const API_BASE = process.env.REACT_APP_API_BASE_URL || "http://localhost:8000";

function formatPct(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return "0.0%";
  return `${num.toFixed(1)}%`;
}

export default function DashboardPage() {
  const [campaigns, setCampaigns] = useState([]);
  const [selectedCampaignId, setSelectedCampaignId] = useState(null);
  const [dashboard, setDashboard] = useState(null);
  const [loadingCampaigns, setLoadingCampaigns] = useState(true);
  const [loadingDashboard, setLoadingDashboard] = useState(false);
  const [error, setError] = useState("");

  const loadCampaigns = async () => {
    try {
      setError("");
      setLoadingCampaigns(true);

      const res = await fetch(`${API_BASE}/api/campaigns`);
      if (!res.ok) {
        throw new Error(`Campaigns API error: ${res.status}`);
      }

      const data = await res.json();
      const list = Array.isArray(data)
        ? data
        : Array.isArray(data?.data)
          ? data.data
          : [];

      setCampaigns(list);

      const normalizedIds = list
        .map((c) => Number(c.campaign_id ?? c.id))
        .filter((id) => Number.isFinite(id));

      if (normalizedIds.length > 0) {
        setSelectedCampaignId((prev) => {
          const prevId = Number(prev);
          if (Number.isFinite(prevId) && normalizedIds.includes(prevId)) {
            return prevId;
          }
          return normalizedIds[0];
        });
      } else {
        setSelectedCampaignId(null);
        setDashboard(null);
      }
    } catch (err) {
      console.error("Campaigns fetch error:", err);
      setError("Failed to load campaigns.");
      setCampaigns([]);
      setSelectedCampaignId(null);
      setDashboard(null);
    } finally {
      setLoadingCampaigns(false);
    }
  };

  const loadDashboard = async (campaignId) => {
    const id = Number(campaignId);
    if (!Number.isFinite(id)) return;

    try {
      setError("");
      setLoadingDashboard(true);

      const res = await fetch(`${API_BASE}/api/dashboard?campaign_id=${id}`);

      if (!res.ok) {
        throw new Error(`Dashboard API error: ${res.status}`);
      }

      const data = await res.json();
      setDashboard(data || null);
    } catch (err) {
      console.error("Dashboard fetch error:", err);
      setError("Failed to load dashboard.");
      setDashboard(null);
    } finally {
      setLoadingDashboard(false);
    }
  };

  useEffect(() => {
    loadCampaigns();
  }, []);

  useEffect(() => {
    if (!selectedCampaignId) return;

    let active = true;

    const run = async () => {
      if (!active) return;
      await loadDashboard(selectedCampaignId);
    };

    run();

    const intervalId = setInterval(run, 5000);

    return () => {
      active = false;
      clearInterval(intervalId);
    };
  }, [selectedCampaignId]);

  const selectedCampaign = useMemo(() => {
    return (
      campaigns.find(
        (c) => Number(c.campaign_id ?? c.id) === Number(selectedCampaignId)
      ) || null
    );
  }, [campaigns, selectedCampaignId]);

  if (loadingCampaigns && !campaigns.length) {
    return (
      <div style={{ padding: "20px", color: "#fff" }}>
        Loading campaigns...
      </div>
    );
  }

  return (
    <div style={{ padding: "20px", color: "#fff" }}>
      <h2 style={{ marginBottom: "12px" }}>Outreach Dashboard</h2>

      {error ? (
        <div style={{ marginBottom: "16px", color: "red" }}>
          {error}
        </div>
      ) : null}

      <div style={{ marginBottom: "20px" }}>
        <label style={{ display: "block", marginBottom: "8px" }}>
          Campaign:
        </label>

        <select
          value={selectedCampaignId ?? ""}
          onChange={(e) => {
            const next = Number(e.target.value);
            setSelectedCampaignId(Number.isFinite(next) ? next : null);
          }}
          style={{
            padding: "8px",
            minWidth: "280px",
            borderRadius: "8px",
          }}
        >
          {campaigns.map((c) => {
            const id = Number(c.campaign_id ?? c.id);
            const name = c.campaign_name || c.name || `Campaign ${id}`;

            return (
              <option key={id} value={id}>
                {name} (ID: {id})
              </option>
            );
          })}
        </select>
      </div>

      {loadingDashboard && !dashboard ? (
        <div>Loading dashboard...</div>
      ) : dashboard ? (
        <div style={{ marginBottom: "24px" }}>
          <p>
            <strong>Campaign:</strong>{" "}
            {dashboard.campaign_name ||
              selectedCampaign?.campaign_name ||
              selectedCampaign?.name ||
              `Campaign ${dashboard.campaign_id}`}
          </p>
          <p>
            <strong>Campaign ID:</strong>{" "}
            {dashboard.campaign_id ?? selectedCampaignId ?? "N/A"}
          </p>
          <p>
            <strong>Emails Sent:</strong> {dashboard.emails_sent || 0}
          </p>
          <p>
            <strong>SMS Sent:</strong> {dashboard.sms_sent || 0}
          </p>
          <p>
            <strong>LinkedIn Sent:</strong> {dashboard.linkedin_sent || 0}
          </p>
          <p>
            <strong>Calls Made:</strong> {dashboard.calls_made || 0}
          </p>

          <p>
            <strong>Open Rate:</strong> {formatPct(dashboard.open_rate)}
          </p>
          <p>
            <strong>Click Rate:</strong> {formatPct(dashboard.click_rate)}
          </p>
          <p>
            <strong>Reply Rate:</strong> {formatPct(dashboard.reply_rate)}
          </p>
          <p>
            <strong>Conversion Rate:</strong>{" "}
            {formatPct(dashboard.conversion_rate)}
          </p>

          <h3 style={{ marginTop: "18px" }}>Funnel</h3>
          <p>
            <strong>Sent:</strong> {dashboard.funnel?.total_sent || 0}
          </p>
          <p>
            <strong>Replied:</strong> {dashboard.funnel?.replied || 0}
          </p>
          <p>
            <strong>Converted:</strong> {dashboard.funnel?.converted || 0}
          </p>
          <p>
            <strong>Drop-off to Reply:</strong>{" "}
            {formatPct(dashboard.funnel?.drop_off_to_reply_pct)}
          </p>
          <p>
            <strong>Drop-off to Conversion:</strong>{" "}
            {formatPct(dashboard.funnel?.drop_off_to_conversion_pct)}
          </p>

          <h3 style={{ marginTop: "18px" }}>Recommendations</h3>
          {Array.isArray(dashboard.recommendations) &&
          dashboard.recommendations.length > 0 ? (
            <ul>
              {dashboard.recommendations.map((rec, idx) => (
                <li key={idx} style={{ marginBottom: "0.5rem" }}>
                  {rec}
                </li>
              ))}
            </ul>
          ) : (
            <p>No recommendations yet.</p>
          )}
        </div>
      ) : (
        <p>No dashboard data available.</p>
      )}
    </div>
  );
}