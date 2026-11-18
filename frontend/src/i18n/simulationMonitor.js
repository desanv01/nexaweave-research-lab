const simulationMonitorCopy = {
  en: {
    infoPlaza: 'Info Plaza',
    topicCommunity: 'Topic Community',
    round: 'ROUND',
    time: 'TIME',
    acts: 'ACTS',
    availableActions: 'Available Actions',
    totalEvents: 'TOTAL EVENTS:',
    monitor: 'SIMULATION MONITOR',
    noSimulation: 'NO_SIMULATION',
    user: 'User',
    searchQuery: 'Search Query:',
    upvotedPost: 'Upvoted Post',
    downvotedPost: 'Downvoted Post',
    actionSkipped: 'Action Skipped',
    roundTag: round => `R${round ?? ''}`,
    elapsed: (hours, minutes) => `${hours}h ${minutes}m`,
    repostedFrom: author => `Reposted from @${author}`,
    likedPost: author => `Liked @${author}'s post`,
    replyToPost: id => `Reply to post #${id}`,
    followed: user => `Followed @${user}`,
    roundProgress: (platform, round, totalRounds, simulatedHours, actionCount) => {
      const platformLabel = platform === 'twitter' ? 'Plaza' : 'Community'
      return `[${platformLabel}] R${round}/${totalRounds} | T:${simulatedHours}h | A:${actionCount}`
    },
    clockLocale: 'en-US',
    actions: {
      post: 'POST',
      like: 'LIKE',
      repost: 'REPOST',
      quote: 'QUOTE',
      follow: 'FOLLOW',
      idle: 'IDLE',
      comment: 'COMMENT',
      dislike: 'DISLIKE',
      search: 'SEARCH',
      trend: 'TREND',
      mute: 'MUTE',
      refresh: 'REFRESH',
      upvote: 'UPVOTE',
      downvote: 'DOWNVOTE'
    }
  },
  zh: {
    infoPlaza: '信息广场',
    topicCommunity: '话题社区',
    round: '轮次',
    time: '时长',
    acts: '操作',
    availableActions: '可用操作',
    totalEvents: '事件总数：',
    monitor: '模拟监控',
    noSimulation: '暂无模拟',
    waitingForActions: '正在等待智能体操作…',
    user: '用户',
    searchQuery: '搜索词：',
    upvotedPost: '已为帖子投赞成票',
    downvotedPost: '已为帖子投反对票',
    actionSkipped: '已跳过操作',
    roundTag: round => `第 ${round ?? ''} 轮`,
    elapsed: (hours, minutes) => `${hours}时${minutes}分`,
    repostedFrom: author => `已转发 @${author} 的帖子`,
    likedPost: author => `已点赞 @${author} 的帖子`,
    replyToPost: id => `回复帖子 #${id}`,
    followed: user => `已关注 @${user}`,
    roundProgress: (platform, round, totalRounds, simulatedHours, actionCount) => {
      const platformLabel = platform === 'twitter' ? '信息广场' : '话题社区'
      return `[${platformLabel}] 第 ${round}/${totalRounds} 轮 | 模拟时间：${simulatedHours} 小时 | 操作：${actionCount}`
    },
    clockLocale: 'zh-CN',
    actions: {
      post: '发帖',
      like: '点赞',
      repost: '转发',
      quote: '引用',
      follow: '关注',
      idle: '无操作',
      comment: '评论',
      dislike: '不喜欢',
      search: '搜索',
      trend: '趋势',
      mute: '静音',
      refresh: '刷新',
      upvote: '赞成',
      downvote: '反对'
    }
  },
  ms: {
    infoPlaza: 'Dataran Maklumat',
    topicCommunity: 'Komuniti Topik',
    round: 'PUSINGAN',
    time: 'MASA',
    acts: 'TINDAKAN',
    availableActions: 'Tindakan Tersedia',
    totalEvents: 'JUMLAH PERISTIWA:',
    monitor: 'PEMANTAU SIMULASI',
    noSimulation: 'TIADA SIMULASI',
    user: 'Pengguna',
    searchQuery: 'Carian:',
    upvotedPost: 'Undi naik pada siaran',
    downvotedPost: 'Undi turun pada siaran',
    actionSkipped: 'Tindakan dilangkau',
    roundTag: round => `P${round ?? ''}`,
    elapsed: (hours, minutes) => `${hours}j ${minutes} min`,
    repostedFrom: author => `Disiarkan semula daripada @${author}`,
    likedPost: author => `Menyukai siaran @${author}`,
    replyToPost: id => `Balas siaran #${id}`,
    followed: user => `Mengikuti @${user}`,
    roundProgress: (platform, round, totalRounds, simulatedHours, actionCount) => {
      const platformLabel = platform === 'twitter' ? 'Dataran Maklumat' : 'Komuniti Topik'
      return `[${platformLabel}] Pusingan ${round}/${totalRounds} | Masa simulasi: ${simulatedHours} jam | Tindakan: ${actionCount}`
    },
    clockLocale: 'ms-MY',
    actions: {
      post: 'Siar',
      like: 'Suka',
      repost: 'Siar semula',
      quote: 'Petik',
      follow: 'Ikut',
      idle: 'Tiada tindakan',
      comment: 'Komen',
      dislike: 'Tidak suka',
      search: 'Cari',
      trend: 'Trend',
      mute: 'Senyapkan',
      refresh: 'Segar semula',
      upvote: 'Undi naik',
      downvote: 'Undi turun'
    }
  }
}

const actionLabelKeys = {
  CREATE_POST: 'post',
  REPOST: 'repost',
  LIKE_POST: 'like',
  CREATE_COMMENT: 'comment',
  LIKE_COMMENT: 'like',
  DO_NOTHING: 'idle',
  FOLLOW: 'follow',
  SEARCH_POSTS: 'search',
  QUOTE_POST: 'quote',
  UPVOTE_POST: 'upvote',
  DOWNVOTE_POST: 'downvote'
}

export const simulationMonitorCopyFor = locale => Object.hasOwn(simulationMonitorCopy, locale)
  ? simulationMonitorCopy[locale]
  : simulationMonitorCopy.en

export const simulationActionLabel = (copy, type) => Object.hasOwn(actionLabelKeys, type)
  ? copy.actions[actionLabelKeys[type]]
  : type

const actionClockOptions = { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }

export function formatSimulationActionTime(timestamp, clockLocale) {
  if (!timestamp) return ''
  try {
    const date = new Date(timestamp)
    return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString(clockLocale, actionClockOptions)
  } catch {
    return ''
  }
}
