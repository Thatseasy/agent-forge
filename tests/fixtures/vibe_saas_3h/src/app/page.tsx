"use client";

import { useState } from "react";
import { createClient } from "@supabase/supabase-js";

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "http://127.0.0.1:54321";
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "demo-anon-key";
const supabase = createClient(supabaseUrl, supabaseAnonKey);

export default function HomePage() {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<string>("Ready");

  async function handleJoinWaitlist(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!email.trim()) {
      setStatus("Enter an email first.");
      return;
    }
    const { error } = await supabase.from("waitlist").insert({ email: email.trim() });
    setStatus(error ? `Error: ${error.message}` : "Joined waitlist!");
  }

  return (
    <main style={{ maxWidth: 640, margin: "4rem auto", fontFamily: "sans-serif" }}>
      <h1>PulseBoard SaaS (3h Prototype)</h1>
      <p>Track team velocity and customer signups in one lightweight workspace.</p>
      <form onSubmit={handleJoinWaitlist}>
        <input
          type="email"
          placeholder="founder@example.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <button type="submit">Join Waitlist</button>
      </form>
      <p data-testid="waitlist-status">{status}</p>
    </main>
  );
}
