import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { tga3Api } from "./api";
import type { DialogueMessage } from "./types";

export function useTaskRuntime(taskId: string) {
  const client = useQueryClient();
  const task = useQuery({
    queryKey: ["tga3", "task", taskId],
    queryFn: () => tga3Api.getTask(taskId),
    refetchInterval: 1500,
  });
  const blackboard = useQuery({
    queryKey: ["tga3", "blackboard", taskId],
    queryFn: () => tga3Api.getBlackboard(taskId),
    refetchInterval: 2000,
  });
  const [messages, setMessages] = useState<DialogueMessage[]>([]);
  const [streamState, setStreamState] = useState<"connecting" | "online" | "offline">("connecting");

  useEffect(() => {
    let active = true;
    tga3Api.getDialogue(taskId).then((items) => {
      if (active) setMessages(items);
    }).catch(() => setStreamState("offline"));
    const source = new EventSource(tga3Api.dialogueStreamUrl(taskId));
    source.onopen = () => setStreamState("online");
    source.onerror = () => setStreamState("offline");
    source.onmessage = (event) => {
      const message = JSON.parse(event.data) as DialogueMessage;
      setMessages((current) => current.some((item) => item.id === message.id)
        ? current
        : [...current, message].sort((left, right) => left.seq - right.seq));
      void client.invalidateQueries({ queryKey: ["tga3", "task", taskId] });
      void client.invalidateQueries({ queryKey: ["tga3", "blackboard", taskId] });
    };
    return () => {
      active = false;
      source.close();
    };
  }, [client, taskId]);

  const pendingQuestion = useMemo(() => {
    const answered = new Set(messages
      .filter((item) => item.kind === "user_message" && item.payload.question_id)
      .map((item) => String(item.payload.question_id)));
    return [...messages].reverse().find((item) =>
      item.kind === "question"
      && Boolean(item.payload.question_id)
      && !answered.has(String(item.payload.question_id)));
  }, [messages]);

  const refresh = async () => {
    await Promise.all([task.refetch(), blackboard.refetch()]);
    setMessages(await tga3Api.getDialogue(taskId));
  };

  return { task, blackboard, messages, pendingQuestion, streamState, refresh };
}
