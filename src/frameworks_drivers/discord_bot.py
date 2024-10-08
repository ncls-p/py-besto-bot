import asyncio
import base64
import io
import logging
import random
import re
from typing import List

import discord
from discord.ext import commands

from domain.entities import ConversationHistory
from interface_adapters.api_client import OllamaClient, OpenAIClient
from use_cases.image_processing import ImageProcessor
from use_cases.message_processing import MessageProcessor
from utils import fetch_url_content

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def setup_discord_bot(
    discord_token: str,
    ollama_api_url: str,
    ollama_api_key: str,
    hyperbolic_url: str,
    hyperbolic_api_key: str,
    openai_url: str,
    openai_api_key: str,
):
    intents = discord.Intents.default()
    intents.messages = True
    intents.message_content = True
    bot = commands.Bot(command_prefix="*", intents=intents)

    history = ConversationHistory()
    ollama_client = OllamaClient(ollama_api_url, ollama_api_key)
    openai_client = OpenAIClient(openai_url, openai_api_key)
    message_processor = MessageProcessor(history, ollama_client, openai_client)
    logging.debug("Discord bot setup with clients and message processor.")

    @bot.event
    async def on_message(message: discord.Message) -> None:
        if message.author == bot.user:
            return

        logging.info(f"Message received: {message.content}")

        if message.content.startswith("*image ") and len(message.content) > 7:
            prompt = message.content[7:]
            channel = message.channel

            async with channel.typing():
                hyperbolic_client = OpenAIClient(hyperbolic_url, hyperbolic_api_key)
                image_processor = ImageProcessor(hyperbolic_client)
                logging.debug(f"Generating image for prompt: {prompt}")

                loop = asyncio.get_running_loop()
                image_bytes = await loop.run_in_executor(
                    None, image_processor.generate_image, prompt
                )

                if isinstance(image_bytes, str):
                    image_bytes = base64.b64decode(image_bytes)

                image_stream = io.BytesIO(image_bytes)

                await channel.send(
                    file=discord.File(image_stream, filename="image.png")
                )

        if bot.user is None:
            return
        user_message = (
            message.content.replace(f"<@{bot.user.id}>", "").strip()
            if bot.user.mentioned_in(message)
            else message.content
        )
        logging.debug(f"Processed user message: {user_message}")
        urls = re.findall(r"(https?://\S+)", user_message)
        url_content = ""
        if urls:
            for url in urls:
                url_content += fetch_url_content(url) + "\n"
            logging.debug(f"Fetched URL content: {url_content}")

        image_url = ""
        if message.attachments and bot.user.mentioned_in(message):
            for attachment in message.attachments:
                if any(
                    ext in attachment.url.lower()
                    for ext in [".png", ".jpg", ".jpeg", ".gif"]
                ):
                    image_url = attachment.url
                    break
            logging.debug(f"Image URL from attachments: {image_url}")

        if bot.user.mentioned_in(message) or random.random() < 0.05:
            await message.channel.typing()
            await process_message(message, user_message, urls, url_content, image_url)

    async def process_message(
        message: discord.Message,
        user_message: str,
        urls: List[str],
        url_content: str,
        image_url: str = "",
    ) -> None:
        """
        Process the message and send a response.
        """
        logger.debug(f"Processing message: {user_message}")
        channel_id = message.channel.id
        conversation = history.get_history(channel_id)

        conversation.append(
            {"role": "user", "content": f"{message.author.name}: {user_message}"}
        )

        api_messages = [
            {
                "role": "system",
                "content": (
                    "Tu agis selon les demandes des utilisateurs et tu discutes en utilisant la syntaxe de Discord, "
                    "des emojis, et en parlant comme tes interlocuteurs. Sois créatif et réponds différemment à chaque "
                    "fois, en prenant en compte le contexte et les conversations précédentes. Imite la manière de parler des "
                    "utilisateurs, en utilisant leur ton, leur langage et leur style, ainsi que des emojis, sauf s'ils te "
                    "demandent explicitement de faire autrement. Si les utilisateurs demandent du code, adopte le rôle "
                    "d'un expert et fournis des extraits de code bien écrits, concis et corrects. N'inclus jamais le nom du bot "
                    "ou des mentions d'utilisateur dans tes réponses, sauf si c'est demandé directement ou si tu veux "
                    "t'adresser spécifiquement à quelqu'un."
                ),
            }
        ]

        api_messages.extend(conversation)
        if url_content:
            api_messages.append(
                {"role": "system", "content": f"URL content: {url_content}"}
            )

        recent_messages = [msg async for msg in message.channel.history(limit=1)]
        if recent_messages and recent_messages[0].attachments:
            for attachment in recent_messages[0].attachments:
                if any(
                    ext in attachment.url.lower()
                    for ext in [".png", ".jpg", ".jpeg", ".gif"]
                ):
                    image_url = attachment.url
                    break
            logging.debug(f"Image URL from recent messages: {image_url}")

        response = message_processor.process_message(
            channel_id, api_messages, image_url
        )
        logging.info(f"Response generated: {response}")

        await message.channel.send(response)

    bot.run(discord_token)
