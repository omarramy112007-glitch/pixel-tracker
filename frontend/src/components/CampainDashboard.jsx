// File: frontend/src/components/CampaignDashboard.jsx

import React, { useEffect, useState } from "react";
import axios from "axios";

export default function CampaignDashboard({ campaignId }) {
  const [campaign, setCampaign] = useState(null);
  const [funnel, setFunnel] = useState(null);
  const [insights, setInsights] = useState(null);
  const [channel, setChannel] = useState("");

  useEffect(() => {
    // Campaign metrics
    axios.get(`/dashboard/campaigns/${campaignId}?channel=${channel}`)
      .then(res => setCampaign(res.data))
      .catch(err => console.error(err));

    // Funnel
    axios.get(`/analytics/campaign/${campaignId}/funnel`)
      .then(res => setFunnel(res.data))
      .catch(err => console.error(err));

    // AI Optimization Insights
    axios.get(`/analytics/campaign/${campaignId}/optimize`)
      .then(res => setInsights(res.data))
      .catch(err => console.error(err));
  }, [campaignId, channel]);

  if (!campaign || !funnel || !insights) return <div>Loading...</div>;

  return (
    <div style={{ padding: "1rem", fontFamily: "Arial, sans-serif" }}>
      <h2>{campaign.campaign_name || "Unnamed Campaign"}</h2>

      {/* Channel Filter */}
      <label>
        Filter by channel:{" "}
        <select value={channel} onChange={e => setChannel(e.target.value)}>
          <option value="">All Channels</option>
          <option value="email">Email</option>
          <option value="sms">SMS</option>
          <option value="linkedin">LinkedIn</option>
          <option value="call">Call</option>
        </select>
      </label>

      {/* ---------------- Metrics ---------------- */}
      <h3>Sent Metrics</h3>
      <p><strong>Emails Sent:</strong> {campaign.emails_sent || 0}</p>
      <p><strong>SMS Sent:</strong> {campaign.sms_sent || 0}</p>
      <p><strong>LinkedIn Sent:</strong> {campaign.linkedin_sent || 0}</p>
      <p><strong>Calls Made:</strong> {campaign.calls_made || 0}</p>

      {/* ---------------- Email Performance ---------------- */}
      <h3>Email Performance</h3>
      <p><strong>Open Rate:</strong> {(campaign.open_rate ? campaign.open_rate * 100 : 0).toFixed(1)}%</p>
      <p><strong>Click Rate:</strong> {(campaign.click_rate ? campaign.click_rate * 100 : 0).toFixed(1)}%</p>
      <p><strong>Reply Rate:</strong> {(campaign.reply_rate ? campaign.reply_rate * 100 : 0).toFixed(1)}%</p>
      <p><strong>Conversion Rate:</strong> {(campaign.conversion_rate ? campaign.conversion_rate * 100 : 0).toFixed(1)}%</p>

      {/* 🔥 NEW: Consulting Performance */}
      <h3>Consulting Performance</h3>
      <p><strong>Consulting Leads:</strong> {campaign.consulting_leads || 0}</p>
      <p><strong>Calls Booked:</strong> {campaign.calls_booked || 0}</p>
      <p><strong>Consulting Conversions:</strong> {campaign.consulting_converted || 0}</p>

      {/* ---------------- Funnel ---------------- */}
      <h3>Conversion Funnel</h3>
      <p><strong>Sent:</strong> {funnel.total_sent || 0}</p>
      <p>
        <strong>Replied:</strong> {funnel.replied || 0} 
        ({funnel.drop_off_to_reply_pct || 0}% drop-off)
      </p>
      <p>
        <strong>Converted:</strong> {funnel.converted || 0} 
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

      {/* ---------------- AI INSIGHTS ---------------- */}
      <h3 style={{ marginTop: "20px" }}>AI Optimization Insights</h3>

      <h4>Problems Detected</h4>
      <ul>
        {insights?.insights?.map((i, idx) => (
          <li key={idx} style={{ color: "red", marginBottom: "0.5rem" }}>{i}</li>
        ))}
      </ul>

      <h4>Recommended Actions</h4>
      <ul>
        {insights?.recommended_actions?.map((a, idx) => (
          <li key={idx} style={{ color: "green", marginBottom: "0.5rem" }}>{a}</li>
        ))}
      </ul>

      {/* ---------------- Recommendations ---------------- */}
      <h3>Recommendations</h3>
      <ul>
        {campaign?.recommendations?.map((rec, idx) => (
          <li key={idx} style={{ 
            color: rec.includes("Low open") ? "red" : "green",
            marginBottom: "0.5rem"
          }}>{rec}</li>
        ))}
      </ul>
    </div>
  );
}