"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from app.forms import LoginForm
from app.views import (
    dashboard, 
    EtudiantListView, EtudiantCreateView, EtudiantUpdateView, EtudiantDeleteView,
    PromotionListView, PromotionCreateView, PromotionDetailView, PromotionUpdateView, PromotionDeleteView,
    EnseignantListView, EnseignantCreateView, EnseignantUpdateView, EnseignantDeleteView,
    SessionListView, SessionDetailView, SessionCreateView, SessionUpdateView, SessionDeleteView,
    session_participants_pdf,
    CoursListView, CoursCreateView, CoursUpdateView, CoursDeleteView,
    ExamenListView, ExamenCreateView, ExamenUpdateView, ExamenDeleteView,
    fiche_cote, examen_inscrire, examen_desinscrire, examen_inscrire_tous
)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', auth_views.LoginView.as_view(
        authentication_form=LoginForm,
        template_name='registration/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('', dashboard, name='dashboard'),
    
    # URLs Etudiant
    path('etudiants/', EtudiantListView.as_view(), name='etudiant_list'),
    path('etudiants/add/', EtudiantCreateView.as_view(), name='etudiant_create'),
    path('etudiants/<int:pk>/edit/', EtudiantUpdateView.as_view(), name='etudiant_update'),
    path('etudiants/<int:pk>/delete/', EtudiantDeleteView.as_view(), name='etudiant_delete'),
    
    # URLs Promotion
    path('promotions/', PromotionListView.as_view(), name='promotion_list'),
    path('promotions/add/', PromotionCreateView.as_view(), name='promotion_create'),
    path('promotions/<int:pk>/', PromotionDetailView.as_view(), name='promotion_detail'),
    path('promotions/<int:pk>/edit/', PromotionUpdateView.as_view(), name='promotion_update'),
    path('promotions/<int:pk>/delete/', PromotionDeleteView.as_view(), name='promotion_delete'),

    # URLs Enseignant
    path('enseignants/', EnseignantListView.as_view(), name='enseignant_list'),
    path('enseignants/add/', EnseignantCreateView.as_view(), name='enseignant_create'),
    path('enseignants/<int:pk>/edit/', EnseignantUpdateView.as_view(), name='enseignant_update'),
    path('enseignants/<int:pk>/delete/', EnseignantDeleteView.as_view(), name='enseignant_delete'),

    # URLs Session
    path('sessions/', SessionListView.as_view(), name='session_list'),
    path('sessions/<int:pk>/', SessionDetailView.as_view(), name='session_detail'),
    path('sessions/<int:pk>/participants.pdf', session_participants_pdf, name='session_participants_pdf'),
    path('sessions/add/', SessionCreateView.as_view(), name='session_create'),
    path('sessions/<int:pk>/edit/', SessionUpdateView.as_view(), name='session_update'),
    path('sessions/<int:pk>/delete/', SessionDeleteView.as_view(), name='session_delete'),
    
    # URLs Cours
    path('cours/', CoursListView.as_view(), name='cours_list'),
    path('cours/add/', CoursCreateView.as_view(), name='cours_create'),
    path('cours/<int:pk>/edit/', CoursUpdateView.as_view(), name='cours_update'),
    path('cours/<int:pk>/delete/', CoursDeleteView.as_view(), name='cours_delete'),

    # URLs Examen
    path('examens/', ExamenListView.as_view(), name='examen_list'),
    path('examens/add/', ExamenCreateView.as_view(), name='examen_create'),
    path('examens/<int:pk>/edit/', ExamenUpdateView.as_view(), name='examen_update'),
    path('examens/<int:pk>/delete/', ExamenDeleteView.as_view(), name='examen_delete'),
    path('examens/<int:pk>/fiche/', fiche_cote, name='examen_fiche'),
    path('examens/<int:pk>/inscrire/', examen_inscrire, name='examen_inscrire'),
    path('examens/<int:pk>/inscrire-tous/', examen_inscrire_tous, name='examen_inscrire_tous'),
    path('examens/<int:pk>/desinscrire/<int:inscription_pk>/', examen_desinscrire, name='examen_desinscrire'),
]
