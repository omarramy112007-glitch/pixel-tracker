// File: frontend/src/components/CampaignDashboard.jsx

import React, { useEffect, useMemo, useState } from "react";
import axios from "axios";

const API_BASE = "http://127.0.0.1:8000";

function unwrapResponse(res) {
  return res?.data?.data ?? res?.data ?? null;
}

export default function CampaignDashboard({ campaignId }) {
  const [campaign, setCampaign] = useState(null);
  const [funnel, setFunnel] = useState(null);
  const [insights, setInsights] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let intervalId;
    let isMounted = true;

    const fetchDashboard = async () => {
      try {
        setError("");

        const [campaignRes, funnelRes, insightsRes] = await Promise.all([
          axios.get(`${API_BASE}/dashboard/campaigns/${campaignId}`),
          axios.get(`${API_BASE}/analytics/campaign/${campaignId}/funnel`),
          axios.get(`${API_BASE}/analytics/campaign/${campaignId}/optimize`),
        ]);

        if (!isMounted) return;

        setCampaign(unwrapResponse(campaignRes));
        setFunnel(unwrapResponse(funnelRes));
        setInsights(unwrapResponse(insightsRes));
        setLoading(false);
      } catch (err) {
        console.error("Dashboard fetch error:", err);
        if (!isMounted) return;
        setError("Failed to load dashboard data.");
        setLoading(false);
      }
    };

    fetchDashboard();
    intervalId = setInterval(fetchDashboard, 3000);

    return () => {
      isMounted = false;
      clearInterval(intervalId);
    };
  }, [campaignId]);

  const metrics = useMemo(() => {
    const emailsSent = campaign?.emails_sent || 0;
    const opens = campaign?.opens || 0;
    const clicks = campaign?.clicks || 0;
    const replies = campaign?.replies || 0;
    const conversions = campaign?.conversions || 0;

    return {
      emailsSent,
      opens,
      clicks,
      replies,
      conversions,
      openRate: emailsSent ? ((opens / emailsSent) * 100).toFixed(1) : "0.0",
      clickRate: emailsSent ? ((clicks / emailsSent) * 100).toFixed(1) : "0.0",
      replyRate: emailsSent ? ((replies / emailsSent) * 100).toFixed(1) : "0.0",
      conversionRate: emailsSent ? ((conversions / emailsSent) * 100).toFixed(1) : "0.0",
    };
  }, [campaign]);

  if (loading) {
    return <div style={{ padding: "1rem" }}>Loading dashboard...</div>;
  }

  if (error) {
    return <div style={{ padding: "1rem", color: "red" }}>{error}</div>;
  }

  if (!campaign) {
    return <div style={{ padding: "1rem" }}>No data available</div>;
  }

  return (
    <div style={{ padding: "1rem", fontFamily: "Arial, sans-serif" }}>
      <h2>{campaign.campaign_name || `Campaign ${campaignId}`}</h2>

      <h3>Sent Metrics</h3>
      <p><strong>Emails Sent:</strong> {metrics.emailsSent}</p>
      <p><strong>SMS Sent:</strong> {campaign.sms_sent || 0}</p>
      <p><strong>LinkedIn Sent:</strong> {campaign.linkedin_sent || 0}</p>
      <p><strong>Calls Made:</strong> {campaign.calls_made || 0}</p>

      <h3>Email Performance</h3>
      <p><strong>Open Rate:</strong> {metrics.openRate}%</p>
      <p><strong>Click Rate:</strong> {metrics.clickRate}%</p>
      <p><strong>Reply Rate:</strong> {metrics.replyRate}%</p>
      <p><strong>Conversion Rate:</strong> {metrics.conversionRate}%</p>

      <h3>Consulting Performance</h3>
      <p><strong>Consulting Leads:</strong> {campaign.consulting_leads || 0}</p>
      <p><strong>Calls Booked:</strong> {campaign.calls_booked || 0}</p>
      <p><strong>Consulting Conversions:</strong> {campaign.consulting_converted || 0}</p>

      <h3>Conversion Funnel</h3>
      {funnel ? (
        <>
          <p><strong>Sent:</strong> {funnel.total_sent || 0}</p>
          <p>
            <strong>Replied:</strong> {funnel.replied || 0}{" "}
            ({funnel.drop_off_to_reply_pct || 0}% drop-off)
          </p>
          <p>
            <strong>Converted:</strong> {funnel.converted || 0}{" "}
            ({funnel.drop_off_to_conversion_pct || 0}% drop-off)
          </p>

          <div style={{ display: "flex", gap: "10px", marginTop: "10px" }}>
            <div style={{ flex: 1, background: "#ddd", padding: "10px", textAlign: "center" }}>
              Sent<br />{funnel.total_sent || 0}
            </div>
            <div style={{ flex: 1, background: "#ffc107", padding: "10px", textAlign: "center" }}>
              Replied<br />{funnel.replied || 0}
            </div>
            <div style={{ flex: 1, background: "#28a745", padding: "10px", textAlign: "center" }}>
              Converted<br />{funnel.converted || 0}
            </div>
          </div>
        </>
      ) : (
        <p>Loading funnel...</p>
      )}

      <h3 style={{ marginTop: "20px" }}>AI Optimization Insights</h3>

      {insights ? (
        <>
          <h4>Problems Detected</h4>
          <ul>
            {(insights.insights || []).map((i, idx) => (
              <li key={idx} style={{ color: "red", marginBottom: "0.5rem" }}>
                {i}
              </li>
            ))}
          </ul>

          <h4>Recommended Actions</h4>
          <ul>
            {(insights.recommended_actions || []).map((a, idx) => (
              <li key={idx} style={{ color: "green", marginBottom: "0.5rem" }}>
                {a}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p>Loading AI insights...</p>
      )}

      <h3>Recommendations</h3>
      <ul>
        {(campaign.recommendations || []).map((rec, idx) => (
          <li
            key={idx}
            style={{
              color: rec.includes("Low open") ? "red" : "green",
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