# Egg Quality Control System

Agentic AI System for Automated Egg Quality Control and Grading.

## Setup

1. **Python Dependencies**
   ```bash
   pip install -r backend/requirements.txt
   ```

2. **Environment Variables**
   Copy `.env.example` to `.env` and fill in your keys:
   ```bash
   copy .env.example .env
   ```
   Add your `GROQ_API_KEY` to the `.env` file.

3. **Custom Models**
   Place your custom EfficientNetB2 model weights in the `backend/models` directory:
   - `backend/models/egg_quality_efficientnetb2.pth`
   - `backend/models/egg_fertility_efficientnetb2.pth`

4. **Initialize Database and Vector Store**
   ```bash
   python setup.py
   ```

5. **Start the Backend**
   ```bash
   uvicorn backend.main:app --reload
   ```

6. **Start the Frontend**
   ```bash
   cd frontend
   npm install
   npm run dev
   ```

## Architecture
- LangGraph ReAct Loop
- Custom PyTorch ViT models
- ChromaDB for vector memory
- React Frontend with SSE streaming
