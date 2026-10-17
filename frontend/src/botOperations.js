// A bot uses one existing browser profile; accounts and jobs retain their server identities.
export function botState(profile, jobs) {
  if (jobs.some(job => job.active_run?.status === 'running' || job.status === 'running')) return 'running';
  if (jobs.some(job => job.active_run?.status === 'queued')) return 'queued';
  if (profile.open) return 'open';
  if (profile.collecting) return 'running';
  if (profile.session_state === 'expired' || profile.at_session?.status === 'signed_out') return 'expired';
  return jobs.length ? 'ready' : 'no_jobs';
}

export function planBotJobs(state, profileIds, action) {
  const ids = new Set(profileIds);
  const profiles = state.profiles.filter(profile => ids.has(profile.id));
  const jobsByProfile = new Map(profiles.map(profile => [profile.id, state.jobs.filter(job => job.profile_id === profile.id)]));
  const jobs = [], skipped = [];
  for (const profile of profiles) {
    const assigned = jobsByProfile.get(profile.id);
    if (!assigned.length) { skipped.push(`${profile.name}: нет заданий`); continue; }
    if (action === 'run' && (profile.open || profile.collecting)) {
      skipped.push(`${profile.name}: ${profile.open ? 'открыт браузер' : 'уже выполняет задание'}`); continue;
    }
    for (const job of assigned) {
      const active = ['queued', 'running'].includes(job.active_run?.status) || job.status === 'running';
      if (action === 'run' ? !active : active) jobs.push(job);
      else skipped.push(`${profile.name} / ${job.name}: ${active ? 'уже в очереди или выполняется' : 'не запущено'}`);
    }
  }
  return { action, jobs, skipped };
}

export async function executeBotJobs(plan, send, onProgress = () => {}) {
  const completed = [], errors = [];
  for (const [index, job] of plan.jobs.entries()) {
    onProgress({ index: index + 1, total: plan.jobs.length, name: job.name });
    try {
      const response = await send(`/jobs/${job.id}/${plan.action}`, {});
      completed.push({ id: job.id, name: job.name, status: response.status });
    } catch (error) {
      // An uncertain request is reported and never retried automatically.
      errors.push({ id: job.id, name: job.name, message: error.message });
    }
  }
  return { ...plan, completed, errors };
}
