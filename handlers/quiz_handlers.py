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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
    
    # Create options keyboard
    keyboard = []
    for i, option in enumerate(question.options):
        callback_data = f"answer_{i}"
        keyboard.append([InlineKeyboardButton(option, callback_data=callback_data)])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Send question
    question_num = session.current_question_index + 1
    total_questions = len(session.quiz.questions)
    
    # Determine which time limit to use for this question
    question_time_limit = question.time_limit if hasattr(question, 'time_limit') and question.time_limit is not None else session.quiz.time_limit
    
    message = update.message.reply_text(
        f"Question {question_num}/{total_questions}:\n\n"
        f"{question.text}\n\n"
        f"⏱️ Time remaining: {question_time_limit} seconds",
        reply_markup=reply_markup
    )
    
    # Store the message ID for later updates
    session.current_message_id = message.message_id
    
    return "ANSWERING"

def send_quiz_question(update, context, chat_id, quiz_data, question_index):
    """
    Send a formatted quiz question with a clean UI similar to the screenshot
    """
    if question_index >= len(quiz_data['questions']):
        finish_quiz(update, context)
        return
    
    # Get current question
    question = quiz_data['questions'][question_index]
    
    # Format the question with modern UI
    question_text = f"<b>{question['question']}</b>\n\n"
    
    # Format the answer options similar to the screenshot
    options_text = ""
    option_letters = ["(a)", "(b)", "(c)", "(d)"]
    
    for i, option in enumerate(question['options']):
        letter = option_letters[i] if i < len(option_letters) else f"({i+1})"
        options_text += f"{letter} {option}\n\n"  # Double newline for spacing
    
    # Add "Anonymous Quiz" subtitle
    subtitle = "Anonymous Quiz"
    
    # Combine all parts
    message_text = f"{question_text}\n{subtitle}\n\n{options_text}"
    
    # Create inline keyboard with circular option buttons
    keyboard = []
    
    for i, option in enumerate(question['options']):
        letter = option_letters[i] if i < len(option_letters) else f"({i+1})"
        # Use a circular button representation with empty center (⚪️)
        button = InlineKeyboardButton(f"⚪️", callback_data=f"answer_{i+1}")
        keyboard.append([button])  # One button per row
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Store current question index
    context.user_data['current_question'] = question_index
    
    # Send the question
    message = context.bot.send_message(
        chat_id=chat_id,
        text=message_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )
    
    # Store message ID for updating later
    context.user_data['question_message_id'] = message.message_id
    
    # Start timer for time limit (if set)
    if 'time_limit' in quiz_data:
        if 'timer_job' in context.user_data:
            context.user_data['timer_job'].schedule_removal()
        
        context.user_data['timer_job'] = context.job_queue.run_once(
            question_timeout, 
            quiz_data['time_limit'], 
            context={'chat_id': chat_id, 'question_index': question_index}
        )
    
    # Initialize response tracking for this question
    if 'responses' not in context.user_data:
        context.user_data['responses'] = {}
    
    context.user_data['responses'][question_index] = {
        'users': {},
        'start_time': time.time()
    }

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
    
    # Record the user's answer
    session.record_answer(selected_option)
    
    # Check if the answer is correct
    correct_option = question['correct_answer']
    is_correct = (selected_option == correct_option)
    
    # Provide feedback to the user
    if is_correct:
        query.answer("✓ Correct!")
    else:
        query.answer("× Wrong!")
    
    # Update the UI to show selected option
    option_letters = ["(a)", "(b)", "(c)", "(d)"]
    
    # Format the question with modern UI
    question_text = f"<b>{question['question']}</b>\n\n"
    
    # Format the answer options
    options_text = ""
    
    for i, option in enumerate(question['options']):
        letter = option_letters[i] if i < len(option_letters) else f"({i+1})"
        options_text += f"{letter} {option}\n\n"  # Double newline for spacing
    
    # Add "Anonymous Quiz" subtitle
    subtitle = "Anonymous Quiz"
    
    # Combine all parts
    message_text = f"{question_text}\n{subtitle}\n\n{options_text}"
    
    # Create updated keyboard with filled circle for selected option
    keyboard = []
    for i, _ in enumerate(question['options']):
        button_text = "⚪️"  # Default empty circle
        if i+1 == selected_option:
            button_text = "🔵"  # Filled circle for selected option
            
        button = InlineKeyboardButton(button_text, callback_data=f"answer_{i+1}")
        keyboard.append([button])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Update the message with new text and keyboard
    query.edit_message_text(
        text=message_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )
    
    # If the session has moved to the next question or finished
    if session.is_quiz_completed():
        # Show final results
        show_quiz_results(update, context, session)
    else:
        # Wait a moment for the user to see their selection
        import time
        time.sleep(1)
        
        # Move to the next question
        next_question = session.get_current_question()
        send_next_question(update, context, user_id, next_question)
    
def send_next_question(update: Update, context: CallbackContext, user_id: int) -> None:
    """Helper function to send the next question after an answer."""
    if user_id not in active_sessions:
        return
    
    session = active_sessions[user_id]
    
    # Create a fake chat object
    class FakeChat:
        def __init__(self, chat_id):
            self.id = chat_id
    
    # Create a fake update object
    class FakeUpdate:
        def __init__(self, effective_chat, effective_user):
            self.effective_chat = effective_chat
            self.effective_user = effective_user
            self.message = None
    
    # Get the chat ID from the callback query
    chat_id = update.callback_query.message.chat_id
    
    fake_chat = FakeChat(chat_id)
    fake_update = FakeUpdate(fake_chat, update.callback_query.from_user)
    
    # Send the next question
    send_quiz_question(fake_update, context, session)

def update_timer(context: CallbackContext) -> None:
    """Update the timer display for a quiz question."""
    job = context.job
    data = job.data
    
    chat_id = data["chat_id"]
    message_id = data["message_id"]
    user_id = data["user_id"]
    question_text = data["question_text"]
    current_question_index = data["question_index"]
    end_time = data["end_time"]
    options_markup = data["reply_markup"]
    
    # Skip if user isn't in active session anymore
    if user_id not in active_sessions:
        return
    
    session = active_sessions[user_id]
    
    # Skip if user has moved on to another question
    if session.current_question_index != current_question_index:
        return
    
    # Calculate remaining time
    remaining_seconds = max(0, int(end_time - time.time()))
    
    # Create countdown display
    if remaining_seconds <= 5:
        # Use large numbers for final countdown
        countdown_display = {
            5: "🕓 5",
            4: "🕓 4",
            3: "🕒 3",
            2: "🕑 2",
            1: "🕐 1",
            0: "⏰ TIME'S UP!"
        }.get(remaining_seconds, str(remaining_seconds))
        
        time_text = f"⚠️ {countdown_display} ⚠️"
    else:
        time_text = f"⏱️ Time remaining: {remaining_seconds} seconds"
    
    # Format the updated message
    question_num = current_question_index + 1
    total_questions = len(session.quiz.questions)
    
    updated_text = (
        f"Question {question_num}/{total_questions}:\n\n"
        f"{question_text}\n\n"
        f"{time_text}"
    )
    
    try:
        # Update the message with the new timer
        context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=updated_text,
            reply_markup=options_markup
        )
        
        # Schedule next update if more than 0 seconds remain
        if remaining_seconds > 0:
            # Update more frequently in the last 10 seconds
            next_update = 1 if remaining_seconds <= 10 else 3
            context.job_queue.run_once(
                update_timer,
                next_update,
                data=data
            )
    except Exception as e:
        # If updating fails, don't break the quiz - just log the error
        logging.error(f"Error updating timer: {str(e)}")
        # Don't schedule more updates if there was an error

def time_up(context: CallbackContext) -> None:
    """Handle time's up for a quiz question."""
    job = context.job
    data = job.data
    
    user_id = data["user_id"]
    chat_id = data["chat_id"]
    question_index = data["question_index"]
    
    # Skip if user isn't in active session anymore
    if user_id not in active_sessions:
        return
    
    session = active_sessions[user_id]
    
    # Skip if user has moved on to another question
    if session.current_question_index != question_index:
        return
    
    # Create a fake update to handle the time up event
    class FakeUser:
        def __init__(self, user_id):
            self.id = user_id
    
    class FakeMessage:
        def __init__(self, chat_id):
            self.chat_id = chat_id
            self.text = f"Time's up! You didn't answer in time."
            
        def reply_text(self, text, reply_markup=None):
            return context.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                reply_markup=reply_markup
            )
    
    class FakeChat:
        def __init__(self, chat_id):
            self.id = chat_id
    
    class FakeCallbackQuery:
        def __init__(self, from_user, message):
            self.from_user = from_user
            self.message = message
            self.data = f"time_up_{question_index}"
            
        def answer(self, text):
            pass
            
        def edit_message_text(self, text, reply_markup=None):
            return context.bot.edit_message_text(
                chat_id=self.message.chat_id,
                message_id=session.current_message_id,
                text=text,
                reply_markup=reply_markup
            )
    
    class FakeUpdate:
        def __init__(self, callback_query, effective_user, message, effective_chat):
            self.callback_query = callback_query
            self.effective_user = effective_user
            self.message = message
            self.effective_chat = effective_chat
    
    fake_user = FakeUser(user_id)
    fake_message = FakeMessage(chat_id)
    fake_chat = FakeChat(chat_id)
    fake_callback_query = FakeCallbackQuery(fake_user, fake_message)
    fake_update = FakeUpdate(fake_callback_query, fake_user, fake_message, fake_chat)
    
    # Add time up button
    keyboard = [[InlineKeyboardButton("Continue", callback_data=f"time_up_{question_index}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Update the message
    question = session.get_current_question()
    if question:
        context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=session.current_message_id,
            text=f"Time's up! You didn't answer in time.\n\nThe correct answer was: {chr(65 + question.correct_option)}. {question.options[question.correct_option]}",
            reply_markup=reply_markup
        )
    
    # Record no answer (-1)
    session.record_answer(-1, False)

def time_up_callback(update: Update, context: CallbackContext) -> str:
    """Handle time up callback query."""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Check if the user is in an active quiz session
    if user_id not in active_sessions:
        query.answer("You are not currently taking a quiz.")
        query.edit_message_text("This quiz has expired. Use /take to start a new quiz.")
        return
    
    session = active_sessions[user_id]
    
    # Move to the next question
    session.move_to_next_question()
    
    # Answer the callback
    query.answer("Moving to next question...")
    
    # Check if there are more questions
    if session.get_current_question():
        # Create a fake message object for send_quiz_question
        class FakeMessage:
            def __init__(self, chat_id):
                self.chat_id = chat_id
                
            def reply_text(self, text, reply_markup=None):
                return context.bot.send_message(
                    chat_id=self.chat_id,
                    text=text,
                    reply_markup=reply_markup
                )
        
        # Create a fake update object with the fake message
        class FakeUpdate:
            def __init__(self, message, effective_chat, effective_user):
                self.message = message
                self.effective_chat = effective_chat
                self.effective_user = effective_user
        
        fake_message = FakeMessage(query.message.chat_id)
        fake_update = FakeUpdate(
            fake_message,
            query.message.chat,
            query.from_user
        )
        
        # Send the next question
        send_quiz_question(fake_update, context, session)
    else:
        # End the quiz
        end_quiz(update, context, session)
    
    return "ANSWERING"

def end_quiz(update: Update, context: CallbackContext, session: QuizSession) -> None:
    """End the quiz and show results."""
    user_id = session.user_id
    
    # Calculate final score
    score = session.calculate_score()
    max_score = len(session.quiz.questions)
    
    # Get the user
    user = get_user(user_id)
    
    # Format the results message
    result_message = f"Quiz: {session.quiz.title}\n\n"
    result_message += f"Final score: {score}/{max_score} "
    result_message += f"({score/max_score*100:.1f}%)\n\n"
    
    # Add a summary of answers
    result_message += "Summary of your answers:\n"
    for i, (question, answer) in enumerate(zip(session.quiz.questions, session.answers)):
        result_message += f"{i+1}. "
        if answer['selected_option'] == -1:
            result_message += "❌ No answer\n"
        elif answer['is_correct']:
            result_message += "✅ Correct\n"
        else:
            result_message += "❌ Incorrect\n"
    
    # Inform about negative marking
    result_message += f"\nNegative marking factor: {session.quiz.negative_marking_factor}"
    
    # Add button to get PDF results
    keyboard = [[InlineKeyboardButton("Get PDF Results", callback_data=f"quiz_pdf_{session.quiz.id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Record the quiz result in the database
    record_quiz_result(user_id, session.quiz.id, score, max_score, session.answers)
    
    # Send the results
    if update.callback_query:
        update.callback_query.edit_message_text(result_message, reply_markup=reply_markup)
    else:
        context.bot.send_message(chat_id=user_id, text=result_message, reply_markup=reply_markup)
    
    # Remove the active session
    if user_id in active_sessions:
        del active_sessions[user_id]

def cancel_quiz(update: Update, context: CallbackContext) -> int:
    """Cancel the current quiz."""
    user_id = update.effective_user.id
    
    if user_id in active_sessions:
        del active_sessions[user_id]
        update.message.reply_text("Quiz canceled. Use /list to see available quizzes.")
    else:
        update.message.reply_text("You are not currently taking a quiz.")
    
    return -1  # End the conversation

def get_results(update: Update, context: CallbackContext) -> None:
    """Send quiz results to user in PDF format."""
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    # Get the user's quiz results
    results = get_user_quiz_results(user_id)
    
    if not results:
        update.message.reply_text("You haven't taken any quizzes yet.")
        return
    
    # Generate PDF
    pdf_buffer = generate_result_pdf(user_id, user.username or user.first_name or str(user_id), results)
    
    # Send the PDF
    update.message.reply_document(
        document=pdf_buffer,
        filename=f"quiz_results_{user_id}.pdf",
        caption="Here are your quiz results."
    )

def quiz_callback(update: Update, context: CallbackContext) -> None:
    """Handle quiz-related callback queries."""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Parse the callback data
    data = query.data.split('_')
    if len(data) < 3:
        query.answer("Invalid callback data")
        return
    
    action = data[1]
    quiz_id = data[2]
    
    if action == "pdf":
        # Generate and send PDF results
        user = get_user(user_id)
        results = get_user_quiz_results(user_id)
        
        # Filter results for specific quiz if needed
        if quiz_id != "all":
            results = [r for r in results if r['quiz_id'] == quiz_id]
        
        if not results:
            query.answer("No results found")
            return
        
        # Generate PDF
        pdf_buffer = generate_result_pdf(user_id, user.username or user.first_name or str(user_id), results)
        
        # Answer the callback
        query.answer("Generating PDF results...")
        
        # Send the PDF
        context.bot.send_document(
            chat_id=user_id,
            document=pdf_buffer,
            filename=f"quiz_results_{user_id}.pdf",
            caption="Here are your quiz results."
        )
    else:
        query.answer("Unknown action")

def import_quiz(update: Update, context: CallbackContext) -> str:
    """Import a quiz from a file."""
    user_id = update.effective_user.id
    
    # Check if the user is an admin
    if user_id not in ADMIN_USERS:
        update.message.reply_text("Sorry, only admins can import quizzes.")
        return
    
    # Check if this is the initial command or file upload
    if update.message.document:
        # User has uploaded a file
        document = update.message.document
        
        # Check the file type (should be JSON)
        if not document.file_name.endswith('.json'):
            update.message.reply_text("Please upload a JSON file.")
            return "IMPORTING"
        
        # Download the file
        file = context.bot.get_file(document.file_id)
        
        # Process the file
        try:
            # Download the file content
            file_content = BytesIO()
            file.download(out=file_content)
            file_content.seek(0)
            
            # Parse the JSON
            quiz_data = json.loads(file_content.read().decode('utf-8'))
            
            # Import the quiz
            quiz = import_quiz_from_file(quiz_data, user_id)
            
            if quiz:
                update.message.reply_text(
                    f"Quiz imported successfully!\n\n"
                    f"Title: {quiz.title}\n"
                    f"Description: {quiz.description}\n"
                    f"Questions: {len(quiz.questions)}\n"
                    f"ID: {quiz.id}\n\n"
                    f"Use /list to see all quizzes."
                )
            else:
                update.message.reply_text("Failed to import quiz. Invalid format.")
        
        except Exception as e:
            logger.error(f"Error importing quiz: {e}")
            update.message.reply_text(f"Error importing quiz: {str(e)}")
        
        return
    else:
        # Initial command
        update.message.reply_text(
            "Please upload a JSON file with your quiz data.\n\n"
            "The file should have the following format:\n"
            "{\n"
            '  "title": "Quiz Title",\n'
            '  "description": "Quiz Description",\n'
            '  "time_limit": 60,\n'
            '  "negative_marking_factor": 0.25,\n'
            '  "questions": [\n'
            '    {\n'
            '      "text": "Question text",\n'
            '      "options": ["Option A", "Option B", "Option C", "Option D"],\n'
            '      "correct_option": 0,\n'
            '      "time_limit": 30\n'
            '    },\n'
            '    ...\n'
            '  ]\n'
            "}\n\n"
            "Use /cancel to cancel."
        )
        
        return "IMPORTING"
    
