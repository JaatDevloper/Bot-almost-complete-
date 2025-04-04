#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Handlers for user-facing quiz functionality
"""

import json
import logging
import time
import os
from datetime import datetime
from io import BytesIO

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from telegram.ext import CallbackContext

from models.user import User
from utils.database import (
    get_quiz, get_quizzes, get_user, record_quiz_result,
    get_user_quiz_results
)
from utils.quiz_manager import QuizSession, import_quiz_from_file
from utils.pdf_generator import generate_result_pdf
from config import ADMIN_USERS

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Store active sessions by user_id
active_sessions = {}

def start(update: Update, context: CallbackContext) -> None:
    """Send a welcome message when the command /start is issued."""
    try:
        user = update.effective_user
        
        # Create a simpler welcome message with basic formatting
        welcome_message = (
            "🎓 Welcome to Telegram Quiz Bot! 🎓\n\n"
            f"Hello {user.first_name}! I'm your interactive quiz companion.\n\n"
            "🚀 Key Features:\n"
            "• 📋 Multiple choice quizzes\n"
            "• ⏱️ Custom time limits per question\n"
            "• 📊 Negative marking for wrong answers\n"
            "• 📑 PDF generation of results\n"
            "• 📤 Import/Export quizzes\n\n"
            "📝 Commands:\n"
            "• /start - Show this welcome message\n"
            "• /help - Get help information\n"
            "• /list - List available quizzes\n"
            "• /take [quiz_id] - Start a quiz\n"
            "• /cancel - Cancel operation\n"
            "• /results - Get quiz results as PDF\n\n"
            "👨‍💻 Created by: @JaatCoderX\n\n"
            "Use /list to see available quizzes!"
        )
        # Use plain text for compatibility
        update.message.reply_text(welcome_message)
    except Exception as e:
        import logging
        logging.error(f"Error in start command: {str(e)}")
        update.message.reply_text("Welcome to the Quiz Bot! Use /help to see available commands.")

def help_command(update: Update, context: CallbackContext) -> None:
    """Send a help message when the command /help is issued."""
    commands = [
        "/start - Start the bot",
        "/help - Show this help message",
        "/list - List all available quizzes",
        "/take (quiz_id) - Take a specific quiz",
        "/results - Get your quiz results",
        "/admin - Show admin commands (admin only)",
    ]
    
    update.message.reply_text(
        'Here are the available commands:\n\n' + '\n'.join(commands)
    )

def list_quizzes(update: Update, context: CallbackContext) -> None:
    """List all available quizzes."""
    quizzes = get_quizzes()
    
    if not quizzes:
        update.message.reply_text("There are no quizzes available yet.")
        return
    
    # Create a list of quiz info
    quiz_list = []
    for quiz_id, quiz in quizzes.items():
        quiz_list.append(f"ID: {quiz_id} - {quiz.title}")
        quiz_list.append(f"Description: {quiz.description}")
        quiz_list.append(f"Questions: {len(quiz.questions)}")
        quiz_list.append(f"Time limit: {quiz.time_limit}s per question")
        quiz_list.append("")
    
    # Send the list
    update.message.reply_text(
        'Available Quizzes:\n\n' + '\n'.join(quiz_list) +
        '\nUse /take (quiz_id) to take a quiz.'
    )

def display_question_with_circular_ui(update, context, question, session):
    """
    Display quiz question with modern circular UI
    
    Args:
        update: Telegram update object
        context: CallbackContext object
        question: Question dictionary
        session: Quiz session object
    """
    # Format the question with modern UI
    question_text = f"<b>{question.text}</b>\n\n"
    
    # Format options with letters
    options_text = ""
    option_letters = ["(a)", "(b)", "(c)", "(d)"]
    
    for i, option in enumerate(question.options):
        letter = option_letters[i] if i < len(option_letters) else f"({i+1})"
        options_text += f"{letter} {option}\n\n"  # Double newline for spacing
    
    # Add "Anonymous Quiz" subtitle
    subtitle = "Anonymous Quiz"
    
    # Add question number if provided
    question_num = session.current_question_index + 1
    total_questions = len(session.quiz.questions)
    counter_text = f"Question {question_num}/{total_questions}"
    
    # Combine all parts
    message_text = f"{counter_text}\n\n{question_text}\n{subtitle}\n\n{options_text}"
    
    # Create inline keyboard with circular option buttons
    keyboard = []
    
    for i in range(len(question.options)):
        # Use a circular button representation with empty center
        button = InlineKeyboardButton(f"⚪️", callback_data=f"answer_{i}")
        keyboard.append([button])  # One button per row
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Send the question
    if update.callback_query:
        message = update.callback_query.edit_message_text(
            text=message_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    else:
        message = update.message.reply_text(
            text=message_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    
    # Store message ID for timer updates
    session.current_message_id = message.message_id
    
    # Set up timer for time limit
    question_time_limit = getattr(question, 'time_limit', None) or session.quiz.time_limit
    
    if question_time_limit > 0:
        # Calculate end time
        end_time = time.time() + question_time_limit
        
        # Store timer data
        timer_data = {
            "user_id": session.user_id,
            "chat_id": update.effective_chat.id,
            "message_id": session.current_message_id,
            "question_text": question.text,
            "question_index": session.current_question_index,
            "end_time": end_time,
            "reply_markup": reply_markup
        }
        
        # Schedule first timer update
        context.job_queue.run_once(
            update_timer,
            3,  # Update every 3 seconds initially
            data=timer_data
        )
        
        # Schedule time's up callback
        context.job_queue.run_once(
            time_up,
            question_time_limit,
            data={
                "user_id": session.user_id,
                "chat_id": update.effective_chat.id,
                "question_index": session.current_question_index
            }
        )

def take_quiz(update: Update, context: CallbackContext) -> str:
    """Start a quiz for a user."""
    user_id = update.effective_user.id
    
    # Check if the user is already in a quiz
    if user_id in active_sessions:
        update.message.reply_text(
            "You are already taking a quiz. Please finish it or use /cancel to cancel it."
        )
        return "ANSWERING"
    
    # Check if quiz ID was provided
    if not context.args:
        update.message.reply_text(
            "Please provide a quiz ID. Use /list to see available quizzes."
        )
        return 
    
    quiz_id = context.args[0]
    quiz = get_quiz(quiz_id)
    
    if not quiz:
        update.message.reply_text(
            f"Quiz with ID {quiz_id} not found. Use /list to see available quizzes."
        )
        return
    
    # Create a new session
    session = QuizSession(user_id, quiz)
    active_sessions[user_id] = session
    
    # Start the quiz
    update.message.reply_text(
        f"Starting quiz: {quiz.title}\n\n"
        f"Description: {quiz.description}\n"
        f"Number of questions: {len(quiz.questions)}\n"
        f"Time limit per question: {quiz.time_limit} seconds\n"
        f"Negative marking: {quiz.negative_marking_factor} points\n\n"
        "Use /cancel to cancel the quiz."
    )
    
    # Get the first question
    question = session.get_current_question()
    
    # Display the first question with circular UI
    display_question_with_circular_ui(update, context, question, session)
    
    return "ANSWERING"

def answer_callback(update: Update, context: CallbackContext):
    """Process user's answer to quiz question"""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Check if the user is in an active session
    if user_id not in active_sessions:
        query.answer("You are not currently taking a quiz.")
        query.edit_message_text("Please start a quiz first using /take command.")
        return
    
    session = active_sessions[user_id]
    
    # Extract the selected option
    selected_option = int(query.data.split('_')[1])
    
    # Get the current question
    question = session.get_current_question()
    if not question:
        query.answer("This question is no longer active.")
        return
    
    # Check if the answer is correct
    correct_option = question.correct_option
    is_correct = (selected_option == correct_option)
    
    # Record the user's answer
    session.record_answer(selected_option)
    
    # Provide feedback to the user
    if is_correct:
        query.answer("✓ Correct!")
    else:
        query.answer("× Wrong!")
    
    # Format the question with modern UI
    question_text = f"<b>{question.text}</b>\n\n"
    
    # Format options with letters
    options_text = ""
    option_letters = ["(a)", "(b)", "(c)", "(d)"]
    
    for i, option in enumerate(question.options):
        letter = option_letters[i] if i < len(option_letters) else f"({i+1})"
        options_text += f"{letter} {option}\n\n"  # Double newline for spacing
    
    # Add "Anonymous Quiz" subtitle
    subtitle = "Anonymous Quiz"
    
    # Add question number
    question_num = session.current_question_index + 1
    total_questions = len(session.quiz.questions)
    counter_text = f"Question {question_num}/{total_questions}"
    
    # Combine all parts
    message_text = f"{counter_text}\n\n{question_text}\n{subtitle}\n\n{options_text}"
    
    # Create updated keyboard with filled circle for selected option
    keyboard = []
    for i in range(len(question.options)):
        button_text = "⚪️"  # Default empty circle
        if i == selected_option:
            button_text = "🔵"  # Filled circle for selected option
            
        button = InlineKeyboardButton(button_text, callback_data=f"answer_{i}")
        keyboard.append([button])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Update the message with new text and keyboard
    query.edit_message_text(
        text=message_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )
    
    # Wait a moment for the user to see their selection
    import time
    time.sleep(1)
    
    # Move to the next question or finish quiz
    session.move_to_next_question()
    next_question = session.get_current_question()
    
    if next_question:
        # Display next question with circular UI
        display_question_with_circular_ui(update, context, next_question, session)
    else:
        # End of quiz, show results
        end_quiz(update, context, session)

