// File: frontend/src/pages/DashboardPage.jsx

import React, { useEffect, useState } from "react";
import CampaignDashboard from "../components/CampaignDashboard";

export default function DashboardPage() {
  const [analytics, setAnalytics] = useState({
    emailsSent: 0,
    openRate: 0,
    replyRate: 0,
    clickRate: 0,
    funnel: [],
  });

  const [loading, setLoading] = useState(true);

  // 🔹 dynamic backend URL (NO HARDCODING)
  const BASE_URL =
    process.env.REACT_APP_API_BASE_URL || "http://localhost:8000";

  // 🔹 fetch analytics from backend
  const fetchAnalytics = async () => {
    try {
      const res = await fetch(
        `${BASE_URL}/api/dashboard/analytics?campaign_id=1`
      );

      if (!res.ok) {
        throw new Error(`API Error: ${res.status}`);
      }

      const data = await res.json();

      setAnalytics({
        emailsSent: data.emails_sent ?? 0,
        openRate: data.open_rate ?? 0,
        replyRate: data.reply_rate ?? 0,
        clickRate: data.click_rate ?? 0,
        funnel: data.funnel ?? [],
      });

      setLoading(false);
    } catch (err) {
      console.error("Dashboard fetch error:", err);

      // still stop loading to avoid infinite spinner
      setLoading(false);
    }
  };

  // 🔹 initial load + polling (safe cleanup)
  useEffect(() => {
    let isMounted = true;

    const load = async () => {
      if (!isMounted) return;
      await fetchAnalytics();
    };

    load();

    const interval = setInterval(() => {
      fetchAnalytics();
    }, 5000);

    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, []);

  // 🔹 loading UI
  if (loading) {
    return (
      <div style={{ padding: "20px", color: "#fff" }}>
        Loading dashboard...
      </div>
    );
  }

  return (
    <CampaignDashboard
      campaignId={1}
      analytics={analytics}
    />
  );
}