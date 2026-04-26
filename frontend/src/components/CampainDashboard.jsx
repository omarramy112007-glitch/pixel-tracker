// File: frontend/src/components/CampaignDashboard.jsx

import React, { useEffect, useMemo, useState } from "react";
import axios from "axios";

const API_BASE = "http://127.0.0.1:8000/analytics";
const DEFAULT_CAMPAIGN_ID = 1;

function safeNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function emptyCampaign(campaignId) {
  return {
    campaign_id: campaignId,
    campaign_name: `Campaign ${campaignId}`,
    total_leads: 0,
    emails_sent: 0,
    sms_sent: 0,
    linkedin_sent: 0,
    calls_made: 0,
    consulting_leads: 0,
    calls_booked: 0,
    consulting_converted: 0,
    opens: 0,
    clicks: 0,
    replies: 0,
    conversions: 0,
    open_rate: 0,
    click_rate: 0,
    reply_rate: 0,
    conversion_rate: 0,
    recommendations: [],
    funnel: {
      total_sent: 0,
      replied: 0,
      converted: 0,
      drop_off_to_reply_pct: 0,
      drop_off_to_conversion_pct: 0,
    },
    metrics: {
      emails_sent: 0,
      sms_sent: 0,
      linkedin_sent: 0,
      calls_made: 0,
      opens: 0,
      clicks: 0,
      replies: 0,
      conversions: 0,
      open_rate: 0,
      click_rate: 0,
      reply_rate: 0,
      conversion_rate: 0,
    },
  };
}

function normalizeDashboardPayload(payload, campaignId) {
  const raw = payload?.data?.data ?? payload?.data ?? payload ?? {};
  const base = emptyCampaign(campaignId);

  const funnel = raw?.funnel ?? base.funnel;

  return {
    ...base,
    ...raw,
    campaign_id: raw?.campaign_id ?? campaignId,
    campaign_name:
      raw?.campaign_name ||
      raw?.name ||
      base.campaign_name,
    funnel: {
      ...base.funnel,
      ...(funnel || {}),
    },
    recommendations: Array.isArray(raw?.recommendations)
      ? raw.recommendations
      : [],
    metrics: {
      ...base.metrics,
      ...(raw?.metrics || {}),
    },
  };
}

async function fetchWithFallback(urls) {
  let lastError = null;

  for (const url of urls) {
    try {
      const res = await axios.get(url);
      return res;
    } catch (err) {
      lastError = err;
    }
  }

  throw lastError;
}

export default function CampaignDashboard({ campaignId = DEFAULT_CAMPAIGN_ID }) {
  const effectiveCampaignId = campaignId || DEFAULT_CAMPAIGN_ID;

  const [campaign, setCampaign] = useState(emptyCampaign(effectiveCampaignId));
  const [funnel, setFunnel] = useState(emptyCampaign(effectiveCampaignId).funnel);
  const [insights, setInsights] = useState({ insights: [], recommended_actions: [] });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [channel, setChannel] = useState("");

  useEffect(() => {
    let alive = true;
    let intervalId;

    const fetchAll = async () => {
      try {
        setError("");

        const dashboardUrls = [
          `${API_BASE}/dashboard/campaign/${effectiveCampaignId}${channel ? `?channel=${encodeURIComponent(channel)}` : ""}`,
          `${API_BASE}/dashboard/campaigns/${effectiveCampaignId}${channel ? `?channel=${encodeURIComponent(channel)}` : ""}`,
          `${API_BASE}/dashboard?campaign_id=${effectiveCampaignId}${channel ? `&channel=${encodeURIComponent(channel)}` : ""}`,
        ];

        const funnelUrls = [
          `${API_BASE}/campaign/${effectiveCampaignId}/funnel`,
          `${API_BASE}/dashboard/campaign/${effectiveCampaignId}/funnel`,
        ];

        const insightsUrls = [
          `${API_BASE}/campaign/${effectiveCampaignId}/optimize`,
          `${API_BASE}/dashboard/campaign/${effectiveCampaignId}/optimize`,
        ];

        const [dashboardRes, funnelRes, insightsRes] = await Promise.all([
          fetchWithFallback(dashboardUrls),
          fetchWithFallback(funnelUrls).catch(() => null),
          fetchWithFallback(insightsUrls).catch(() => null),
        ]);

        if (!alive) return;

        const normalizedCampaign = normalizeDashboardPayload(dashboardRes?.data, effectiveCampaignId);
        console.log("DASHBOARD RESPONSE:", normalizedCampaign);
        setCampaign(normalizedCampaign);

        if (funnelRes?.data) {
          const funnelData = funnelRes.data?.funnel ?? funnelRes.data?.data ?? funnelRes.data ?? {};
          setFunnel({
            ...emptyCampaign(effectiveCampaignId).funnel,
            ...funnelData,
          });
        } else {
          setFunnel(normalizedCampaign.funnel || emptyCampaign(effectiveCampaignId).funnel);
        }

        if (insightsRes?.data) {
          const insightsData = insightsRes.data?.data ?? insightsRes.data ?? {};
          setInsights({
            insights: Array.isArray(insightsData?.insights) ? insightsData.insights : [],
            recommended_actions: Array.isArray(insightsData?.recommended_actions)
              ? insightsData.recommended_actions
              : [],
          });
        } else {
          setInsights({ insights: [], recommended_actions: [] });
        }

        setLoading(false);
      } catch (err) {
        console.error("Dashboard fetch error:", err);
        if (!alive) return;
        setError("Failed to load dashboard.");
        setCampaign(emptyCampaign(effectiveCampaignId));
        setFunnel(emptyCampaign(effectiveCampaignId).funnel);
        setInsights({ insights: [], recommended_actions: [] });
        setLoading(false);
      }
    };

    fetchAll();
    intervalId = setInterval(fetchAll, 5000);

    return () => {
      alive = false;
      clearInterval(intervalId);
    };
  }, [effectiveCampaignId, channel]);

  const metrics = useMemo(() => {
    const emailsSent = safeNumber(campaign?.emails_sent ?? campaign?.metrics?.emails_sent);
    const opens = safeNumber(campaign?.opens ?? campaign?.metrics?.opens);
    const clicks = safeNumber(campaign?.clicks ?? campaign?.metrics?.clicks);
    const replies = safeNumber(campaign?.replies ?? campaign?.metrics?.replies);
    const conversions = safeNumber(campaign?.conversions ?? campaign?.metrics?.conversions);

    const openRate =
      campaign?.open_rate ?? (emailsSent ? (opens / emailsSent) * 100 : 0);
    const clickRate =
      campaign?.click_rate ?? (emailsSent ? (clicks / emailsSent) * 100 : 0);
    const replyRate =
      campaign?.reply_rate ?? (emailsSent ? (replies / emailsSent) * 100 : 0);
    const conversionRate =
      campaign?.conversion_rate ?? (emailsSent ? (conversions / emailsSent) * 100 : 0);

    return {
      emailsSent,
      opens,
      clicks,
      replies,
      conversions,
      openRate: safeNumber(openRate).toFixed(1),
      clickRate: safeNumber(clickRate).toFixed(1),
      replyRate: safeNumber(replyRate).toFixed(1),
      conversionRate: safeNumber(conversionRate).toFixed(1),
    };
  }, [campaign]);

  if (loading) {
    return <div style={{ padding: "1rem" }}>Loading dashboard...</div>;
  }

  if (error) {
    return <div style={{ padding: "1rem", color: "red" }}>{error}</div>;
  }

  const campaignName =
    campaign?.campaign_name ||
    campaign?.name ||
    `Campaign ${effectiveCampaignId}`;

  const recommendations = Array.isArray(campaign?.recommendations)
    ? campaign.recommendations
    : [];

  const currentFunnel = funnel || emptyCampaign(effectiveCampaignId).funnel;

  return (
    <div style={{ padding: "1rem", fontFamily: "Arial, sans-serif" }}>
      <h2>Outreach Dashboard</h2>
      <h3>
        Campaign: {campaignName} (ID: {campaign?.campaign_id ?? effectiveCampaignId})
      </h3>

      <label style={{ display: "block", marginBottom: "1rem" }}>
        Filter by channel:{" "}
        <select value={channel} onChange={(e) => setChannel(e.target.value)}>
          <option value="">All Channels</option>
          <option value="email">Email</option>
          <option value="sms">SMS</option>
          <option value="linkedin">LinkedIn</option>
          <option value="call">Call</option>
        </select>
      </label>

      <h3>Sent Metrics</h3>
      <p><strong>Emails Sent:</strong> {metrics.emailsSent}</p>
      <p><strong>SMS Sent:</strong> {safeNumber(campaign?.sms_sent ?? campaign?.metrics?.sms_sent)}</p>
      <p><strong>LinkedIn Sent:</strong> {safeNumber(campaign?.linkedin_sent ?? campaign?.metrics?.linkedin_sent)}</p>
      <p><strong>Calls Made:</strong> {safeNumber(campaign?.calls_made ?? campaign?.metrics?.calls_made)}</p>

      <h3>Email Performance</h3>
      <p><strong>Open Rate:</strong> {metrics.openRate}%</p>
      <p><strong>Click Rate:</strong> {metrics.clickRate}%</p>
      <p><strong>Reply Rate:</strong> {metrics.replyRate}%</p>
      <p><strong>Conversion Rate:</strong> {metrics.conversionRate}%</p>

      <h3>Consulting Performance</h3>
      <p><strong>Consulting Leads:</strong> {safeNumber(campaign?.consulting_leads)}</p>
      <p><strong>Calls Booked:</strong> {safeNumber(campaign?.calls_booked)}</p>
      <p><strong>Consulting Conversions:</strong> {safeNumber(campaign?.consulting_converted)}</p>

      <h3>Conversion Funnel</h3>
      <p><strong>Sent:</strong> {safeNumber(currentFunnel.total_sent)}</p>
      <p>
        <strong>Replied:</strong> {safeNumber(currentFunnel.replied)}{" "}
        ({safeNumber(currentFunnel.drop_off_to_reply_pct)}% drop-off)
      </p>
      <p>
        <strong>Converted:</strong> {safeNumber(currentFunnel.converted)}{" "}
        ({safeNumber(currentFunnel.drop_off_to_conversion_pct)}% drop-off)
      </p>

      <div style={{ display: "flex", gap: "10px", marginTop: "10px" }}>
        <div style={{ flex: 1, background: "#ddd", padding: "10px", textAlign: "center" }}>
          Sent<br />{safeNumber(currentFunnel.total_sent)}
        </div>
        <div style={{ flex: 1, background: "#ffc107", padding: "10px", textAlign: "center" }}>
          Replied<br />{safeNumber(currentFunnel.replied)}
        </div>
        <div style={{ flex: 1, background: "#28a745", padding: "10px", textAlign: "center" }}>
          Converted<br />{safeNumber(currentFunnel.converted)}
        </div>
      </div>

      <h3 style={{ marginTop: "20px" }}>AI Insights</h3>
      <h4>Problems Detected</h4>
      <ul>
        {(insights?.insights || []).map((i, idx) => (
          <li key={idx} style={{ color: "red", marginBottom: "0.5rem" }}>
            {i}
          </li>
        ))}
      </ul>

      <h4>Recommended Actions</h4>
      <ul>
        {(insights?.recommended_actions || []).map((a, idx) => (
          <li key={idx} style={{ color: "green", marginBottom: "0.5rem" }}>
            {a}
          </li>
        ))}
      </ul>

      <h3>Recommendations</h3>
      <ul>
        {recommendations.map((rec, idx) => (
          <li
            key={idx}
            style={{
              color: String(rec).includes("Low open") ? "red" : "green",
              marginBottom: "0.5rem",
            }}
          >
            {rec}
          </li>
        ))}
      </ul>
    </div>
  );
}